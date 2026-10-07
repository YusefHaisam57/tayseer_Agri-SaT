# ============================================================
# test_platform_integrity.py
# Automated platform verification suite for Tayseer Agri-SaT
# ============================================================
import os
import sys
import py_compile
import unittest

sys.stdout.reconfigure(encoding='utf-8')

class TestTayseerPlatformIntegrity(unittest.TestCase):

    def test_01_syntax_compilation(self):
        """Ensure all key Python modules compile cleanly under Python 3.12"""
        modules = [
            'app.py',
            'ai_core.py',
            'farm_data_provider.py',
            'risk_engine.py',
            'ml_gee_engine.py',
            'rag_engine.py',
            'multimodal_engine.py',
            'gee_integration.py',
            'omni_bot.py',
            'config.py',
            'database.py'
        ]
        for mod in modules:
            with self.subTest(module=mod):
                self.assertTrue(os.path.exists(mod), f"File {mod} not found")
                try:
                    py_compile.compile(mod, doraise=True)
                except Exception as e:
                    self.fail(f"Syntax error in {mod}: {e}")

    def test_02_flask_app_routes(self):
        """Verify all critical 10 unified pages and APIs are registered without collision"""
        from app import app
        
        registered_rules = [r.rule for r in app.url_map.iter_rules()]
        
        expected_pages = [
            '/',
            '/dashboard',
            '/risks',
            '/chat',
            '/farm-setup',
            '/map-view',
            '/reports',
            '/carbon',
            '/integrations',
            '/settings',
            '/admin'
        ]
        
        expected_apis = [
            '/api/chat',
            '/api/system/integrations-status',
            '/api/risks/dashboard',
            '/api/webhook/whatsapp',
            '/api/webhook/telegram'
        ]
        
        for p in expected_pages:
            with self.subTest(page=p):
                self.assertIn(p, registered_rules, f"Missing route for page: {p}")
                
        for a in expected_apis:
            with self.subTest(api=a):
                self.assertIn(a, registered_rules, f"Missing route for API: {a}")

    def test_03_ai_core_grounding(self):
        """Verify ask_agri_assistant returns meta and uses FarmDataProvider"""
        from ai_core import ask_agri_assistant
        
        res = ask_agri_assistant(1, "ما هي حالة الري في المزرعة؟", return_meta=True)
        self.assertIsInstance(res, dict)
        self.assertIn('response', res)
        self.assertIn('provider', res)
        self.assertIn('confidence', res)
        self.assertIn('sources', res)
        self.assertTrue(len(res['response']) > 0)

    def test_04_ml_provenance(self):
        """Verify ML crop health predictions carry SYNTHETIC_TRAINED provenance"""
        from ml_gee_engine import predict_crop_health
        
        pred = predict_crop_health(ndvi=0.65, lst=28.0, et=3.5, return_dict=True)
        self.assertIsInstance(pred, dict)
        self.assertIn('SYNTHETIC_TRAINED', pred.get('model_provenance', ''))
        self.assertIn('predicted_status', pred)
        self.assertIn('predicted_label_ar', pred)

    def test_05_integrations_status_api(self):
        """Verify /api/system/integrations-status returns live diagnostic payload"""
        from app import app
        client = app.test_client()
        
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['email'] = 'test@tayseer.com'
            
        res = client.get('/api/system/integrations-status')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsInstance(data, dict)
        self.assertIn('statuses', data)
        self.assertIn('gee', data['statuses'])
        self.assertIn('owm', data['statuses'])
        self.assertIn('llm', data['statuses'])
        self.assertIn('database', data['statuses'])

    def test_06_templates_sidebar(self):
        """Verify standard sidebar is included across all core platform templates"""
        core_templates = [
            'templates/dashboard.html',
            'templates/risks.html',
            'templates/ai_assistant.html',
            'templates/map_view.html',
            'templates/reports.html',
            'templates/carbon_monitoring.html',
            'templates/integrations.html',
            'templates/settings.html',
            'templates/profile.html',
            'templates/email_settings.html',
            'templates/whatsapp.html',
            'templates/telegram.html',
            'templates/host_inbox.html'
        ]
        for t in core_templates:
            with self.subTest(template=t):
                with open(t, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.assertIn('sidebar.html', content, f"{t} does not include sidebar.html")

if __name__ == '__main__':
    unittest.main(verbosity=2)
