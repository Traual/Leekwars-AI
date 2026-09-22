"""Checks the independent experiment's accounting, not scoring implementation."""
import unittest
from native_oracle import terminal_value, analyse


class OracleAccounting(unittest.TestCase):
    def test_identity_not_order_after_opponent_kill(self):
        self.assertEqual(terminal_value({'terminal':[[1,2700,2700,1],[0,0,2000,0]]}),-12700)

    def test_death_before_ai_turn_does_not_need_a_debug(self):
        self.assertEqual(terminal_value({'terminal':[[0,600,2000,0],[1,0,2700,1]],'outcome':{'logs':{}}}),10600)

    def test_nonterminal_health_difference(self):
        self.assertEqual(terminal_value({'terminal':[[1,200,2700,1],[0,600,2000,0]]}),400)

    def test_incomplete_seed_pair_is_not_counted(self):
        records=[dict(case='a',seed=1,option=0,valid=True,root=[1],score=[20],terminal=30)]
        self.assertEqual(analyse([dict(id='a',family='test')],records)[0]['pairs'],0)

    def test_roots_must_match(self):
        records=[dict(case='a',seed=1,option=i,valid=True,root=[i],score=[20],terminal=30) for i in (0,1)]
        with self.assertRaises(AssertionError):analyse([dict(id='a',family='test')],records)

    def test_invalid_cast_excludes_entire_pair(self):
        records=[dict(case='a',seed=1,option=i,valid=i==0,root=[1],score=[20],terminal=30) for i in (0,1)]
        self.assertEqual(analyse([dict(id='a',family='test')],records)[0]['pairs'],0)


if __name__=='__main__': unittest.main()
