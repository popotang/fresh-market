import unittest
from unittest.mock import Mock,patch
from datetime import date,datetime
from zoneinfo import ZoneInfo
from kemai_pipeline.state import stream_state
from kemai_pipeline.planner import incremental_plan

class DurableStateTests(unittest.TestCase):
 def row(self,day,field='source_created_at'):
  return {'window_key':day,'row_count':0,'evidence':{'source_rows':0,'window_time_field':field,'verification_method':'empty_page_and_db'}}
 @patch('kemai_pipeline.state.cached_days',return_value=set())
 def test_rebuild_without_local_cache_preserves_holes(self,cache):
  c=Mock();c.read_all.return_value=[self.row('2026-08-01'),self.row('2026-08-03')]
  state=stream_state(c,'retail-sales',date(2026,8,4))
  self.assertTrue(state['baseline_verified']);self.assertEqual(state['last_contiguous_verified_day'],'2026-08-01')
  plan=incremental_plan({'retail-sales':state},datetime(2026,8,5,12,tzinfo=ZoneInfo('Asia/Shanghai')))
  self.assertEqual([r['day'] for r in plan['tasks']],['2026-08-05','2026-08-02','2026-08-04'])
  self.assertEqual(c.read_all.call_args.args[0],'sync_pipeline_windows')
 @patch('kemai_pipeline.state.cached_days',return_value=set())
 def test_wrong_time_basis_does_not_bootstrap_full_history(self,cache):
  c=Mock();c.read_all.return_value=[self.row('2026-08-01','sold_at')]
  state=stream_state(c,'retail-sales',date(2026,8,4));self.assertFalse(state['baseline_verified'])
  plan=incremental_plan({'retail-sales':state},datetime(2026,8,5,12,tzinfo=ZoneInfo('Asia/Shanghai')))
  self.assertEqual([r['mode'] for r in plan['tasks']],['provisional']);self.assertTrue(plan['blocked'])
 @patch('kemai_pipeline.state.cached_days',return_value=set())
 def test_zero_rows_need_actual_window_evidence(self,cache):
  c=Mock();c.read_all.return_value=[]
  self.assertFalse(stream_state(c,'retail-sales',date(2026,8,4))['baseline_verified'])

if __name__=='__main__':unittest.main()
