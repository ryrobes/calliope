import contextlib
import json
import unittest
from unittest.mock import patch
import ui_decisions as ui

class Decisions(unittest.TestCase):
    def body(self):return {'state':{'notes':'fictional'},'tree':{'kind':'leaf','label':'clarify'}}
    def test_bounds_and_no_sql_or_urls(self):
        for field,value in [('sql','SELECT 1'),('url','http://evil'),('max_steps',5),('state',{'a':'x'*24001}),('tree',{'kind':'code','question':'execute'})]:
            body=self.body();body[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):ui.validate(body)
        self.assertEqual(ui.validate(self.body())[2],4)
    def test_tree_routes_thresholds_and_depth(self):
        for tree in [{'kind':'branch','question':'q','yes':{'kind':'leaf','label':'a'},'no':{'kind':'leaf','label':'b'},'threshold':float('nan')},
                     {'kind':'choice','question':'q','choices':{'a':'A','b':'B'},'routes':{'a':{'kind':'leaf','label':'a'}}}]:
            body=self.body();body['tree']=tree
            with self.assertRaises(ValueError):ui.validate(body)
    def test_raw_branch_gets_constructor_default_without_mutating_definition(self):
        body=self.body();body['tree']={'kind':'branch','question':'Is this a return visit?',
            'yes':{'kind':'leaf','label':'yes'},'no':{'kind':'leaf','label':'no'}}
        self.assertEqual(ui.validate(body)[1]['threshold'],.5)
        self.assertNotIn('threshold',body['tree'])
    def test_parameterized_query_with_bounded_timeout(self):
        calls=[]
        class DB:
            def transaction(self):return contextlib.nullcontext()
            def execute(self,sql,params=None):calls.append((sql,params));return self
            def fetchone(self):return {'decision':{'status':'complete','label':'clarify'}}
        result=ui.evaluate(lambda:contextlib.nullcontext(DB()),self.body())
        self.assertEqual(result['decision']['label'],'clarify')
        self.assertEqual(calls[1][0],'SELECT rvbbit.decision_tree(%s::jsonb,%s::jsonb,%s) AS decision')
        self.assertEqual(json.loads(calls[1][1][0]),self.body()['state'])
        self.assertIn('8000ms',calls[0][0])
    def test_capacity_is_released_after_database_error(self):
        def fail():raise RuntimeError('offline')
        for _ in range(5):
            with self.assertRaises(RuntimeError):ui.evaluate(fail,self.body())
        self.assertTrue(ui.SLOTS.acquire(blocking=False));ui.SLOTS.release()

class SqlValidationParity(unittest.TestCase):
    def test_blank_questions_outcomes_and_choice_keys_fail_before_sql(self):
        leaf={'kind':'leaf','label':'yes'}
        cases=[{'kind':'leaf','label':'   '}, {'kind':'branch','question':' ','yes':leaf,'no':leaf},
               {'kind':'choice','question':'q','choices':{'':'empty','b':'B'},'routes':{'':leaf,'b':leaf}},
               {'kind':'choice','question':'q','choices':{'a'*81:'A','b':'B'},'routes':{'a'*81:leaf,'b':leaf}},
               {'kind':'choice','question':'q','choices':{'a':' ','b':'B'},'routes':{'a':leaf,'b':leaf}}]
        for tree in cases:
            with self.subTest(tree=tree),self.assertRaises(ValueError):ui.validate({'state':{},'tree':tree})
