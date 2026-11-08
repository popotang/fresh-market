import json
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
from kemai_pipeline.contracts import Batch
from kemai_pipeline.planner import incremental_plan
from kemai_pipeline.audit import check_history, source_day
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter, ContractError
from kemai_pipeline.capabilities import capture_method


class BatchTests(unittest.TestCase):
    def batch(self, capture="c1", value=1):
        return Batch("t", "s", "w", capture, 0, "v1", ({"source_key":"1", "value":value},)).envelope()

    def test_retry_identity_stable_and_content_change_detectable(self):
        a, b = self.batch(), self.batch(value=2)
        self.assertEqual(a["batch_id"], self.batch()["batch_id"])
        self.assertEqual(a["batch_id"], b["batch_id"])
        self.assertNotEqual(a["payload_hash"], b["payload_hash"])

    def test_new_capture_does_not_reuse_receipt(self):
        self.assertNotEqual(self.batch()["batch_id"], self.batch("c2")["batch_id"])

    def test_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            Batch("t","s","w","c",0,"v",({"source_key":"1"},{"source_key":"1"})).envelope()


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,18,12,tzinfo=ZoneInfo("Asia/Shanghai"))
        self.state = {"baseline_verified":True,"baseline_from":"2026-08-01",
                      "last_contiguous_verified_day":"2026-11-07"}

    def test_long_outage_is_bounded_daily_catchup(self):
        result = incremental_plan({"retail-sales":self.state},self.now,max_days=2)
        self.assertEqual([r["day"] for r in result["tasks"]], ["2026-11-18","2026-11-08","2026-11-09"])

    def test_display_maximum_never_used_as_cursor(self):
        result=incremental_plan({"retail-sales":{"covered_through":"2026-11-17"}},self.now)
        self.assertEqual([t['mode'] for t in result["tasks"]],['provisional'])
        self.assertEqual(result["blocked"][0]["reason"],"baseline_evidence_required")

    def test_older_hole_is_repaired_before_forward_backlog(self):
        self.state["holes"]=[{"from":"2026-10-11","to":"2026-10-11"}]
        tasks=incremental_plan({"retail-sales":self.state},self.now)["tasks"]
        self.assertEqual(tasks[1]["day"],"2026-10-11")

    def test_cooldown_and_user_pause_issue_no_tasks(self):
        result=incremental_plan({"retail-sales":self.state},self.now,
                                cooldown_until=self.now+timedelta(minutes=20))
        self.assertEqual(result["tasks"],[])
        self.assertEqual(incremental_plan({"retail-payments":self.state},self.now)["tasks"],[])

    def test_excel_count_only_never_selected_for_retail_data(self):
        self.assertEqual(capture_method("retail-sales",export_mapping_verified=True),
                         "api_for_data+export_for_count")


class AdapterTests(unittest.TestCase):
    def client(self, responses):
        executor=Mock(side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(
            {"ok":True,"data":{"result":response}})) for response in responses])
        return LovrabetAdapter({"appcode":"app-test","tenant_id":"t","datasets":{"table":"code"}},
                               executor=executor),executor

    def page(self, rows, total, page=1):
        return {"tableData":rows,"paging":{"currentPage":page,"pageSize":100,"totalCount":total}}

    def test_explicit_app_and_tenant_scope_no_shell(self):
        client,executor=self.client([self.page([{"id":1,"tenant_id":"t"}],1)])
        self.assertEqual(len(client.read_all("table")),1)
        args=executor.call_args.args[0]
        self.assertEqual(args[args.index("--appcode")+1],"app-test")
        self.assertNotIn("shell",executor.call_args.kwargs)

    def test_tenant_leak_and_truncation_rejected(self):
        for response in [self.page([{"id":1,"tenant_id":"other"}],1),self.page([],1)]:
            client,_=self.client([response])
            with self.assertRaises(ContractError): client.read_all("table")

    def test_changing_total_rejected(self):
        rows=[{"id":i,"tenant_id":"t"} for i in range(100)]
        client,_=self.client([self.page(rows,101),self.page([{"id":101,"tenant_id":"t"},
                             {"id":102,"tenant_id":"t"}],102,2)])
        with self.assertRaisesRegex(ContractError,"changed"): client.read_all("table")

    def test_duplicate_page_ids_rejected(self):
        rows=[{"id":i,"tenant_id":"t"} for i in range(100)]
        client,_=self.client([self.page(rows,101),self.page([rows[-1]],101,2)])
        with self.assertRaisesRegex(ContractError,"duplicate"): client.read_all("table")


class AuditTests(unittest.TestCase):
    def test_missing_event_is_unknown_even_when_database_empty(self):
        client=Mock();client.read_all.return_value=[];client.count.return_value=0
        result=check_history(client,"retail-sales",date(2026,11,7),date(2026,11,7))
        self.assertEqual(len(result["holes"]),1)
        self.assertIsNone(result["last_contiguous_verified_day"])

    def test_matching_recorded_counts_do_not_claim_global_or_detail_coverage(self):
        client=Mock();client.count.return_value=3
        client.read_all.return_value=[{"id":1,"stream":"retail-sales","source_from":"2026-11-07",
          "source_to":"2026-11-07","status":"success","source_rows":3,"saved_rows":3,
          "coverage_level":"full_list","verification_method":"export_total_and_capture"}]
        result=check_history(client,"retail-sales",date(2026,11,7),date(2026,11,7))
        self.assertEqual(result["holes"],[])
        self.assertEqual(result["detail_coverage"],"not_checked")
        self.assertIsNone(result["last_contiguous_verified_day"])

    def test_shanghai_midnight_epoch(self):
        self.assertEqual(source_day(1793980800000),date(2026,11,7))


if __name__ == "__main__": unittest.main()
