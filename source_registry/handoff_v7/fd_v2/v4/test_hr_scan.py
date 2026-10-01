import unittest,tempfile,pathlib,json,sqlite3,zipfile,subprocess,shutil
from unittest.mock import patch
import hr_scan as h

class Tests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=pathlib.Path(self.tmp.name);self.st=h.Store(self.p/'x.db')
 def tearDown(self):self.st.db.close();self.tmp.cleanup()
 def cfg(self,adapter='article'):return {'source_id':'S1','name':'Fixture','adapter':adapter,'item_url_pattern':r'/proekt/.+','entry_url':'https://example.org/','allowed_hosts':['example.org']}
 def test_freshness_moscow_boundary(self):
  self.assertEqual(h.bucket('2026-09-17T22:00:00Z','2026-10-01'),'last_14_days')
  self.assertEqual(h.bucket('2026-09-17','2026-10-01'),'days_15_30')
  self.assertEqual(h.bucket('2026-09-02','2026-10-01'),'days_15_30')
  self.assertEqual(h.bucket('2026-09-01','2026-10-01'),'older_retained')
 def test_unknown_and_future_dates_retained(self):
  self.assertEqual(h.bucket(None,'2026-10-01'),'undated');self.assertEqual(h.bucket('2026-10-02','2026-10-01'),'future_date_review')
 def test_event_date_not_publication(self):
  raw='<script type="application/ld+json">{"@type":"Event","startDate":"2026-10-02"}</script><main>Training</main>'
  items,_=h.extract_html(raw,'https://example.org/a',self.cfg());self.assertIsNone(items[0]['published_at'])
 def test_article_publication(self):
  raw='<script type="application/ld+json">{"@type":"Article","datePublished":"2026-09-30"}</script><article>Need training</article>'
  items,_=h.extract_html(raw,'https://example.org/a',self.cfg());self.assertTrue(items[0]['published_at'].startswith('2026-09-30'))
 def test_no_navigation_contamination(self):
  items,_=h.extract_html('<nav>Поставка товаров</nav><main>Ищем тренера</main>','https://example.org/a',self.cfg());self.assertNotIn('Поставка',items[0]['text'])
 def test_telegram_segmentation(self):
  raw='<div data-post="demo/17"><div class="tgme_widget_message_text">Ищем тренера</div><time datetime="2026-09-30T10:00:00+03:00"></time></div><div data-post="demo/18"><div class="tgme_widget_message_text">Нужен курс</div></div>'
  items,_=h.extract_html(raw,'https://t.me/s/demo',self.cfg('telegram'));self.assertEqual(len(items),2);self.assertNotEqual(items[0]['document_id'],items[1]['document_id']);self.assertIsNone(items[1]['published_at'])
 def test_list_links_not_leads(self):
  items,_=h.extract_html('<a href="/proekt/1">Training project</a><a href="/privacy">Privacy</a>','https://example.org/',self.cfg('listing'));self.assertEqual(len(items),1);self.assertEqual(items[0]['kind'],'item_link');self.assertFalse(items[0]['buyer_confirmed'])
 def test_reader_error_not_zero_demand(self):
  items,_,status=h.extract_reader({'url':'https://example.org','text':'Internal Error ()\nL0: Failed to fetch'},self.cfg());self.assertFalse(items);self.assertEqual(status['state'],'fetch_failed')
 def test_captcha_not_content(self):
  items,_,status=h.extract_reader({'url':'https://example.org','text':'Are you not a robot?\nL0: CAPTCHA'},self.cfg());self.assertFalse(items);self.assertEqual(status['state'],'access_required')
 def test_reader_feed_not_invented_posts(self):
  items,_,_=h.extract_reader({'url':'https://t.me/s/demo','text':'Demo\nL0: Post one\nL1: Post two'},self.cfg('telegram'));self.assertEqual(len(items),1);self.assertEqual(items[0]['kind'],'feed_snapshot_unsegmented')
 def test_unknown_wording_kept(self):self.assertTrue(h.triage('Нам надо разобраться с этим процессом')['do_not_drop'])
 def test_employment_not_confirmed_order(self):
  r=h.triage('Вакансия: нужен тренер в штат');self.assertEqual(r['signal_type'],'employment_or_contract_review');self.assertFalse(r['buyer_confirmed'])
 def test_corporate_training_not_party(self):self.assertNotIn('adjacent_or_excluded',h.triage('Корпоративное обучение руководителей')['signals'])
 def test_idempotence_revisions_and_notes(self):
  x=h.record('S','https://example.org/1','Test','one');self.assertEqual(self.st.ingest(x),'new');self.assertEqual(self.st.ingest(x),'unchanged')
  self.st.db.execute('INSERT INTO manager_notes VALUES(?,?)',(x['document_id'],'keep'));self.st.db.commit()
  y=h.record('S','https://example.org/1','Test','two');self.assertEqual(self.st.ingest(y),'revised');self.assertEqual(self.st.db.execute('SELECT count(*) FROM revisions').fetchone()[0],2);self.assertEqual(self.st.db.execute('SELECT note FROM manager_notes').fetchone()[0],'keep')
 def test_mirrors_preserved(self):
  a=h.record('S','https://example.org/1','A','same');b=h.record('S','https://example.org/2','B','same');self.assertNotEqual(a['document_id'],b['document_id'])
 def test_export_reply_relationship(self):
  f=self.p/'tg.json';f.write_text(json.dumps({'id':33,'messages':[{'id':1,'type':'message','date':'2026-09-30T10:00:00','text':'Question'},{'id':2,'type':'message','date':'2026-09-30T10:01:00','reply_to_message_id':1,'text':[{'text':'Need training'}]}]}))
  h.ingest_telegram_export(f,'S',self.st,'2026-10-01');rows=[json.loads(x[0]) for x in self.st.db.execute('SELECT payload FROM documents')];self.assertEqual(rows[1]['parent_id'],rows[0]['document_id']);self.assertTrue(rows[0]['url'].startswith('telegram-export://'))
 def test_audit_samples_unmatched(self):
  self.st.ingest(h.record('S','https://example.org/1','A','ordinary words'));out=self.p/'audit.csv';self.assertEqual(h.audit_sample(self.st,out),1);self.assertIn('unmatched_retained',out.read_text())
 def test_url_allowlist(self):
  for u in ['http://example.org/a','https://other.org/a','https://user:pw@example.org/']:
   with self.assertRaises(ValueError):h.guard_url(u,{'example.org'})
 def test_private_dns_rejected(self):
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(ValueError):h.guard_url('https://example.org/',{'example.org'})
 def test_fetch_failure_audited(self):
  with patch.object(h,'robots_allowed',return_value=(True,'test')),patch.object(h,'fetch',side_effect=TimeoutError()):r=h.collect(self.cfg(),self.st,'2026-10-01')
  self.assertFalse(r['coverage_complete']);self.assertEqual(r['errors'][0]['state'],'fetch_failed')
 def test_page_limit_retains_pending(self):
  with patch.object(h,'robots_allowed',return_value=(True,'test')),patch.object(h,'fetch',return_value=('<main>Test</main><a rel="next" href="/next">Next</a>','https://example.org/')):r=h.collect(self.cfg(),self.st,'2026-10-01',1)
  self.assertEqual(r['stop_reason'],'page_limit');self.assertIn('https://example.org/next',r['pending_urls'])
 def test_docx_attachment(self):
  p=self.p/'brief.docx'
  with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="urn:w"><w:p><w:r><w:t>Training request</w:t></w:r></w:p></w:document>')
  self.assertIn('Training request',h.extract_attachment(p))
 def test_xlsx_attachment_shared_strings(self):
  p=self.p/'brief.xlsx'
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Training request</t></si></sst>')
   z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>')
  self.assertIn('A1 Training request',h.extract_attachment(p))
 def test_unsupported_attachment_retained_as_failure(self):
  p=self.p/'brief.exe';p.write_text('anything')
  with self.assertRaises(ValueError):h.extract_attachment(p)
 def make_pdf(self):
  content=b'BT /F1 24 Tf 40 150 Td (TRAINING REQUEST 40 MANAGERS) Tj ET'
  objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 220] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
  data=b'%PDF-1.4\n';offsets=[]
  for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
  xref=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'
  p=self.p/'brief.pdf';p.write_bytes(data);return p
 @unittest.skipUnless(shutil.which('pdftotext'),'pdftotext unavailable')
 def test_pdf_extraction(self):self.assertIn('TRAINING REQUEST',h.extract_attachment(self.make_pdf()))
 @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('tesseract'),'OCR dependencies unavailable')
 def test_english_image_ocr(self):
  p=self.make_pdf();subprocess.run(['pdftoppm','-singlefile','-png','-r','150',str(p),str(self.p/'image')],capture_output=True,check=True,timeout=20)
  text=h.extract_attachment(self.p/'image.png','eng')
  self.assertIn('REQUEST',text);self.assertIn('MANAGERS',text)
if __name__=='__main__':unittest.main(verbosity=2)
