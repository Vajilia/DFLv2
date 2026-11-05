"""Honors from recorded awards only (src/dflsite/honors.py): a synthetic world with the engine's own record shapes."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from dflsite import honors as H


def card(pid, first, last, pos, role='player'):
    return NS(identity=NS(id=pid, first=first, last=last, pos=pos, role=role))


def universe(tiara_year=2000, rings_year=2000, eligible=False):
    cards = {i: card(i, 'P%d' % i, 'Q%d' % i, 'QB') for i in range(1, 8)}
    leaders = {'Alabama': {'hc': card(70, 'Hana', 'Coach', 'hc', 'hc'), 'ceo': card(71, 'Cee', 'Oh', 'ceo', 'ceo')},
               'Baja': {'hc': card(80, 'Bea', 'Hoop', 'hc', 'hc')}}
    clubs = {'Alabama': NS(roster=[1, 2, 3]), 'Baja': NS(roster=[4, 5, 6, 7])}
    w = NS(cards=cards, leaders=leaders, L=NS(clubs=clubs), persona={9: ('Retired Star', 'WR')},
           rings={4: {rings_year}, 5: {rings_year}, 6: {rings_year}, 80: {rings_year}} if rings_year else {},
           tiaras={1: {tiara_year}, 2: {tiara_year}, 71: {tiara_year}, 70: {tiara_year}} if tiara_year else {},
           bowl_final={'Western': {'roster': [4, 5, 6, 7]}, 'Eastern': {'roster': [1, 2, 3]}},
           career={1: {2000: {'score': 3.0, 'club': 'Alabama'}}}, credited={1: {2000}},
           hall={'reg': {('league',): {'QB': [9]}} if eligible else {}, 'ever': {('league',): {9}} if eligible else {}, 'fan': [9] if eligible else [],
                 'log': [{'year': tiara_year, 'scope': ('league',), 'pos': 'WR', 'pid': 9, 'kind': 'fan'}] if eligible else []})
    return NS(world=w, tpl=NS(division_of={}))


TEAMS = {'Alabama': {'abbr': 'ALA', 'name': 'Alabama X'}, 'Baja': {'abbr': 'BAJ', 'name': 'Baja Y'}}


class Honors(unittest.TestCase):
    def test_rings_list_recorded_players_and_staff_and_the_side(self):
        d = H.build_part(universe(), 'rings', 1, TEAMS)
        H.check_public(d)
        self.assertEqual(d['status'], 'recorded')
        self.assertEqual(d['side'], 'Western All-Stars')
        self.assertEqual(d['counts'], {'players': 3, 'staff': 1})
        staff = [p for p in d['recipients'] if 'id' not in p]
        self.assertEqual(staff[0]['role'], 'Head coach')
        self.assertEqual(staff[0]['club'], 'BAJ')

    def test_tiara_names_the_club_and_its_leaders_without_ids(self):
        d = H.build_part(universe(), 'tiara', 1, TEAMS)
        H.check_public(d)
        self.assertEqual(d['club'], 'ALA')
        self.assertEqual([p['role'] for p in d['recipients'] if 'role' in p], ['CEO', 'Head coach'])
        self.assertTrue(all('id' not in p for p in d['recipients'] if 'role' in p))

    def test_empty_halls_say_why_instead_of_inventing_members(self):
        d = H.build_part(universe(), 'halls', 1, TEAMS, {'hall_min_seasons': 5})
        H.check_public(d)
        self.assertEqual(d['halls'], [])
        self.assertIn('5 credited seasons', d['rule'])
        self.assertEqual(d['eligible_players'], 0)
        self.assertTrue(any(u['field'] == 'hall values' for u in d['unavailable']))
        f = H.build_part(universe(), 'fan_entrant', 1, TEAMS)
        self.assertEqual((f['status'], f['entrant']), ('none', None))

    def test_missing_records_are_reported_as_not_recorded(self):
        u = universe(tiara_year=None, rings_year=None)
        for part in ('rings', 'tiara'):
            d = H.build_part(u, part, 1, TEAMS)
            self.assertEqual((d['status'], d['recipients']), ('not_recorded', []))
            self.assertTrue(d['unavailable'])

    def test_halls_and_fan_entrant_when_recorded(self):
        u = universe(eligible=True)
        h = H.build_part(u, 'halls', 1, TEAMS, {'hall_min_seasons': 5})
        H.check_public(h)
        self.assertEqual(h['halls'][0]['scope'], {'level': 'league'})
        self.assertEqual(h['halls'][0]['registers']['QB'][0]['name'], 'Retired Star')
        f = H.build_part(u, 'fan_entrant', 1, TEAMS)
        self.assertEqual((f['status'], f['entrant']['name']), ('recorded', 'Retired Star'))

    def test_unknown_fields_and_private_words_are_refused(self):
        d = H.build_part(universe(), 'rings', 1, TEAMS)
        bad = dict(d, secret_rating=3)
        with self.assertRaises(H.HonorsError):
            H.check_public(bad)
        bad = dict(d, recipients=[dict(d['recipients'][0], true_overall=80)])
        with self.assertRaises(H.HonorsError):
            H.check_public(bad)
        with self.assertRaises(H.HonorsError):
            H.check_public(dict(d, note='the hidden rating decided it'))


if __name__ == '__main__':
    unittest.main()
