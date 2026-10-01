import json,pathlib,tempfile,unittest
from unittest.mock import patch
import source_ops as ops
c=ops.collector
class Tests(unittest.TestCase):
    def test_registry(self):self.assertTrue(ops.validate()['passed'],ops.validate())
    def test_plan(self):
        p=ops.plan(20);self.assertEqual(len({r['source_id'] for r in p}),20);self.assertEqual(sum(r['lane']=='exploration' for r in p),4);self.assertGreaterEqual(len({r['research_track'] for r in p}),8)
    def test_dates(self):
        self.assertEqual(c.bucket('2026-09-19','2026-10-02'),'last_14_days');self.assertEqual(c.bucket('2026-09-18','2026-10-02'),'days_15_30');self.assertEqual(c.bucket(None,'2026-10-02'),'undated')
        docs,_,_=c.extract_reader({'url':'https://example.org','text':'L0: Приглашаем. Форум 25.11.2026'}, {'source_id':'T','name':'T','adapter':'page'});self.assertIsNone(docs[0]['published_at'])
    def test_telegram_and_dedup(self):
        raw='<div data-post="sample/3"><time datetime="2026-09-30T09:00:00+03:00"></time><div class="tgme_widget_message_text">Ищем тренера для команды</div></div>'
        docs,_=c.extract_html(raw,'https://t.me/s/sample',{'source_id':'T','adapter':'telegram'});self.assertEqual(len(docs),1);self.assertIn('2026-09-30',docs[0]['published_at'])
        with tempfile.TemporaryDirectory() as d:
            s=c.Store(str(pathlib.Path(d)/'test.sqlite'));self.assertEqual(s.ingest(docs[0]),'new');self.assertEqual(s.ingest(docs[0]),'unchanged');self.assertEqual(s.db.execute('select count(*) from documents').fetchone()[0],1);s.db.close()
    def test_batch_guards(self):
        with self.assertRaises(ValueError):ops.scan(['X']*21,':memory:','2026-10-02',2)
        with self.assertRaises(ValueError):ops.scan(['UNKNOWN'],':memory:','2026-10-02',2)
        cfg=next(x for x in ops.rows('collector_config') if not x['manual_only'])
        with patch.object(c,'collect',side_effect=RuntimeError('fixture network failure')):
            result=ops.scan([cfg['source_id']],':memory:','2026-10-02',2)
        self.assertEqual(result[0]['state'],'failed_retained');self.assertFalse(result[0]['coverage_complete'])
if __name__=='__main__':unittest.main()
