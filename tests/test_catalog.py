import unittest
from unittest.mock import patch
from funding_collector.catalog import discover
import test_dashboard

class CatalogTests(unittest.TestCase):
    def test_bybit_pagination_and_exact_unit_spot_intersection(self):
        def row(a, **kwargs):
            return dict(baseCoin=a, quoteCoin='USDT', settleCoin='USDT', symbol=a+'USDT',
                        status='Trading', contractType='LinearPerpetual', **kwargs)
        class HTTP:
            def get(self, url, params):
                if params['category']=='spot':
                    result={'list':[row('SUI'),row('PEPE'),row('ARB')]}
                elif not params.get('cursor'):
                    result={'list':[row('SUI'),row('1000PEPE')],'nextPageCursor':'next'}
                else:
                    result={'list':[row('ARB')],'nextPageCursor':''}
                return {'error':None,'data':{'result':result}}
        self.assertEqual(discover('bybit',HTTP()),['ARB','SUI'])

    def test_transport_failure_does_not_become_empty_success(self):
        class HTTP:
            def get(self,*args): return {'error':'HTTP_403','data':None}
        with self.assertRaisesRegex(ValueError,'HTTP_403'):
            discover('bybit',HTTP())

class SelectionTests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    tearDown = test_dashboard.DashboardTests.tearDown
    def test_discovered_asset_collects_only_supported_exchange(self):
        self.app.catalog={'bybit':['SUI'],'gate':['BTC']}
        with patch.object(self.app,'_run'):
            self.app.start({'exchanges':['bybit','gate'],'assets':['SUI']})
            self.app.thread.join()
        markets=self.app.state()['markets']
        self.assertEqual([(m['exchange'],m['symbol']) for m in markets],[('bybit','SUI/USDT')])

    def test_no_supported_market_is_rejected(self):
        self.app.catalog={'bybit':['SUI'],'gate':['BTC']}
        with self.assertRaises(ValueError):
            self.app.start({'exchanges':['gate'],'assets':['SUI']})
