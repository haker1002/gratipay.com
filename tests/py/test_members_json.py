from __future__ import print_function, unicode_literals

import json

import pytest
from gratipay.testing import Harness
from aspen import Response


class Tests(Harness):

    def hit_members_json(self, method='GET', auth_as=None):
        response = self.client.GET('/~Enterprise/members/index.json', auth_as=auth_as)
        return json.loads(response.body)


    @pytest.mark.xfail(reason='migrating to Teams; see #3399')
    def test_team_has_members(self):
        team = self.make_participant('Enterprise', number='plural', claimed_time='now')
        team.add_member(self.make_participant('alice', claimed_time='now'))
        team.add_member(self.make_participant('bob', claimed_time='now'))
        team.add_member(self.make_participant('carl', claimed_time='now'))

        actual = [x['username'] for x in self.hit_members_json()]
        assert actual == ['carl', 'bob', 'alice', 'Enterprise']

    @pytest.mark.xfail(reason='migrating to Teams; see #3399')
    def test_team_admin_can_get_bare_bones_list(self):
        self.make_participant('Enterprise', number='plural', claimed_time='now')
        actual = [x['username'] for x in self.hit_members_json(auth_as='Enterprise')]
        assert actual == ['Enterprise']

    def test_anon_cant_get_bare_bones_list(self):
        self.make_participant('Enterprise', number='plural', claimed_time='now')
        assert pytest.raises(Response, self.hit_members_json).value.code == 404

    def test_non_admin_cant_get_bare_bones_list(self):
        self.make_participant('Enterprise', number='plural', claimed_time='now')
        self.make_participant('alice', claimed_time='now')
        assert pytest.raises(Response, self.hit_members_json, auth_as='alice').value.code == 404
