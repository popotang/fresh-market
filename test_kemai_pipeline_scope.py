import unittest
from kemai_pipeline.source import validate_retail_scope,SourceError
class ScopeTests(unittest.TestCase):
 def scope(self):return {'branchCodes':'1001','branchIdList':['69028303'],'branchId1':'69028303','timeType':'1','memberPhone':'','sourceTypeList':[]}
 def test_observed_branch_and_upload_type(self):validate_retail_scope('retail-sales',self.scope())
 def test_hidden_filter_or_branch_change_rejected(self):
  for patch in [{'memberPhone':'123'}, {'branchIdList':['other']},{'branchId':'other'},{'unknownFilter':'active'}]:
   with self.assertRaises(SourceError):validate_retail_scope('retail-sales',{**self.scope(),**patch})
 def test_orders_use_a_different_time_parameter(self):
  scope=self.scope();scope.pop('timeType');scope['dateType']='0';validate_retail_scope('retail-orders',scope)
  with self.assertRaises(SourceError):validate_retail_scope('retail-orders',self.scope())
if __name__=='__main__':unittest.main()
