"""Transport safety invariants without ROS or a robot."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch
spec=importlib.util.spec_from_file_location('ros_client',Path(__file__).resolve().parents[1]/'docker/agent_instance/ros_client.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Transport(unittest.TestCase):
    def test_uuid_is_reserved_across_process_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            gid=uuid.uuid4().hex;record=dict(session_id='old',endpoint='/robots/a/action')
            m.Journal(tmp).reserve(gid,record)
            restarted=m.Journal(tmp)
            self.assertEqual(restarted.load(gid),record)
            with self.assertRaises(m.Failure) as exc:restarted.reserve(gid,dict(record,session_id='new'))
            self.assertEqual(exc.exception.category,'duplicate_uuid')
    def test_concurrent_uuid_reservation_has_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=m.Journal(tmp);gid=uuid.uuid4().hex
            def reserve(_):
                try:journal.reserve(gid,{});return True
                except m.Failure:return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(reserve,range(20))),1)
    def test_timeout_does_not_cancel_or_reissue_future(self):
        future=concurrent.futures.Future()
        with self.assertRaises(m.Failure) as exc:m.wait(future,.001)
        self.assertEqual(exc.exception.category,'request_timeout')
        self.assertFalse(future.cancelled());future.set_result('late');self.assertEqual(m.wait(future,1),'late')
    def test_reconcile_only_calls_explicit_readonly_status(self):
        client=m.Clients.__new__(m.Clients);client.call=Mock(return_value={'response':{}})
        record=dict(status_endpoint='/robots/a/status',status_type='example/srv/Status')
        self.assertEqual(client.reconcile(record),{'response':{}})
        self.assertEqual(client.call.call_args.args[0]['endpoint'],record['status_endpoint'])
        self.assertEqual(client.call.call_count,1)
    def test_service_failures_are_classified_without_retry(self):
        client=m.Clients.__new__(m.Clients)
        import threading
        client.lock=threading.RLock();client.service_calls={}
        endpoint=Mock();endpoint.srv_type.Request=Mock(return_value=object())
        client.service=Mock(return_value=endpoint)
        client.populate=lambda message,data:message
        client.fields=lambda message:dict(success=message.success)
        request=dict(endpoint='/robots/a/query',type='example/srv/Query',timeout=.001)
        endpoint.wait_for_service.return_value=False
        with self.assertRaises(m.Failure) as exc:client.call(request)
        self.assertEqual(exc.exception.category,'discovery_failure');endpoint.call_async.assert_not_called()
        endpoint.wait_for_service.return_value=True
        endpoint.call_async.return_value=concurrent.futures.Future()
        with self.assertRaises(m.Failure) as exc:client.call(request)
        self.assertEqual(exc.exception.category,'request_timeout')
        self.assertIsNone(client.service_calls['/robots/a/query']['callable'])
        self.assertEqual(client.service_calls['/robots/a/query']['error'],'request_timeout')
        self.assertEqual(endpoint.call_async.call_count,1);endpoint.remove_pending_request.assert_called_once()
        future=concurrent.futures.Future();future.set_result(SimpleNamespace(success=False))
        endpoint.call_async.return_value=future
        with self.assertRaises(m.Failure) as exc:client.call(request)
        self.assertEqual(exc.exception.category,'server_failure')

    def test_status_never_equates_graph_or_liveness_with_control(self):
        import threading,time
        client=m.Clients.__new__(m.Clients);client.lock=threading.RLock()
        client.sessions={};client.service_calls={}
        client.node=SimpleNamespace(count_publishers=Mock(return_value=1))
        client.services={};client.actions={};client.subscriptions={}
        client.graph=lambda:dict(topics=[('/robots/a/state',['std_msgs/msg/String'])],services=[],actions=[])
        sample=dict(session_id='new',state={},control_available=True)
        client.subscribe=lambda *args:dict(message={'data':json.dumps(sample)},age_seconds=.1)
        status=client.status()['robots'][0]
        self.assertTrue(status['process_alive']);self.assertTrue(status['ros_discoverable'])
        self.assertEqual(status['session_id'],'new')
        self.assertTrue(status['control_available']['/robots/a/state'])
        client.service_calls={'/robots/a/status':dict(at=time.monotonic(),callable=True,server_success=True,error=None)}
        sample['session_id']='restarted'
        self.assertEqual(client.status()['robots'][0]['service_callable'],{})
        client.subscribe=lambda *args:dict(message={'data':json.dumps(sample)},age_seconds=4)
        status=client.status()['robots'][0]
        self.assertIsNone(status['process_alive']);self.assertTrue(status['ros_discoverable'])
        self.assertFalse(status['observation_fresh']);self.assertFalse(any(status['control_available'].values()))
        client.node.count_publishers.return_value=0
        self.assertFalse(client.status()['robots'][0]['ros_discoverable'])

    def test_cancel_timeout_reconciles_once_without_resending(self):
        client=m.Clients.__new__(m.Clients)
        gid=uuid.uuid4().hex
        record=dict(endpoint='/robots/a/action',session_id='old')
        client.journal=Mock();client.journal.load.return_value=record
        client.call=Mock(side_effect=m.Failure('request_timeout'))
        client.reconcile=Mock(return_value={'response':{'state':'unknown'}})
        result=client.cancel(dict(goal_id=gid,timeout=.01))
        self.assertFalse(result['replay_allowed']);self.assertEqual(result['outcome'],'unknown')
        client.call.assert_called_once();client.reconcile.assert_called_once_with(record)
        self.assertEqual(client.call.call_args.args[0]['request']['goal_info']['goal_id']['uuid'],list(uuid.UUID(gid).bytes))

    def test_malformed_uuid_cannot_escape_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):m.Journal(tmp).reserve('../../escape',{})
