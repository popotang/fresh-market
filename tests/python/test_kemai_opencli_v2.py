import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from kemai_pipeline.source import OpenCLISource, SourceError, SourceCooling, SourceAuthenticationRequired
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter

class OpenCLIV2Tests(unittest.TestCase):
    def call(self, value, code=0):
        result=SimpleNamespace(stdout=json.dumps(value),stderr='',returncode=code)
        return OpenCLISource(executor=lambda *a,**k:result).call('capabilities')

    def test_both_wire_versions(self):
        for value in ([{'id':1}],{'ok':True,'data':[{'id':1}]}):
            self.assertEqual(self.call(value),[{'id':1}])

    def test_error_and_malformed_envelopes_fail_closed(self):
        for value in ({'ok':False,'error':{'code':'outcome_unknown'}},
                      {'ok':'true','data':[]},{'ok':True,'data':{}},{'data':[]}):
            with self.assertRaises(SourceError):self.call(value)

    @patch('kemai_pipeline.source.record_limit')
    def test_business_limit_200_inside_v2(self, record):
        with self.assertRaises(SourceCooling):
            self.call({'ok':True,'data':[{'code':1,'msg':'您的操作太频繁，请稍后再试'}]})
        record.assert_called_once()

    @patch('kemai_pipeline.source.record_limit')
    def test_v2_nonzero_limit(self, record):
        with self.assertRaises(SourceCooling):self.call({'ok':False,'error':{'code':'RATE_LIMIT'}},1)
        record.assert_called_once()

    def test_authentication_error_from_v2_json_is_actionable(self):
        for code in (0, 1):
            with self.assertRaises(SourceAuthenticationRequired):
                self.call({'ok':False,'error':{'code':'UNKNOWN','message':'Error: 登录认证失败'}},code)

    def test_business_fields_are_not_auth_diagnostics(self):
        self.assertEqual(self.call({'ok':True,'data':[{'name':'未登录','id':1}]}),[{'name':'未登录','id':1}])

    def test_custom_primary_key_filter_update(self):
        calls=[];row={'product_id':7,'payload_hash':'old'}
        def execute(cmd,**kw):
            verb=cmd[2];params=json.loads(cmd[cmd.index('--params')+1]);calls.append((verb,params))
            if verb=='update':row.update(params);result={}
            else:result={'tableData':[dict(row)],'paging':{'currentPage':1,'pageSize':params['pageSize'],'totalCount':1}}
            return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'result':result}}),stderr='')
        adapter=LovrabetAdapter({'tenant_id':'1','appcode':'a','datasets':{'product_profile':'p'},
            'tenantless_datasets':['product_profile'],'primary_keys':{'product_profile':'product_id'}},executor=execute)
        adapter.upsert('product_profile',{'product_id':7,'payload_hash':'new'},unique_keys=['product_id'])
        self.assertEqual(next(p for v,p in calls if v=='update'),{'id':7,'product_id':7,'payload_hash':'new'})
        self.assertEqual(calls[0][1]['orderBy'],[{'product_id':'asc'}])
        self.assertNotIn('id',calls[0][1]['select'])

if __name__=='__main__':unittest.main()
