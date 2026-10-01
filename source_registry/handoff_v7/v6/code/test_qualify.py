import unittest
from qualify import qualify, unit_cost
class QualificationTests(unittest.TestCase):
    def base(self,**kw):
        return dict(evidence='request_body_read',timing='unknown',status='buyer_unconfirmed',misb_fit='core',buying_intent='explicit_training_request',reachability='platform_reply_route_unverified',**kw)
    def test_event_beats_later_platform_deadline(self):
        a=qualify(self.base(event_end='2026-10-02',deadline='2026-10-18'),'2026-10-01')
        self.assertEqual(a['queue'],'urgent_status_check');self.assertEqual(a['effective_last_date'],'2026-10-02')
        self.assertEqual(qualify(self.base(event_end='2026-10-02',deadline='2026-10-18'),'2026-10-03')['queue'],'repeat_cycle')
    def test_generic_page_is_not_request_evidence(self):
        a=self.base();a['evidence']='generic_page';self.assertEqual(qualify(a,'2026-10-01')['queue'],'restore_evidence')
    def test_closed_teaser_stays_repeat_cycle(self):
        a=self.base();a.update(evidence='public_teaser_restricted',status='likely_completed_linkage_inferred');self.assertEqual(qualify(a,'2026-10-01')['queue'],'repeat_cycle')
    def test_missing_denominator_and_real_zero_cost(self):
        self.assertIsNone(unit_cost(None,2));self.assertIsNone(unit_cost(10,0));self.assertEqual(unit_cost(0,2),0);self.assertEqual(unit_cost(100,2),50)
    def test_readable_does_not_mean_ready(self):
        self.assertFalse(qualify(self.base(),'2026-10-01')['proposal_ready'])
if __name__=='__main__':unittest.main()
