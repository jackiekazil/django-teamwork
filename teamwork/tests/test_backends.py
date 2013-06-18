import logging
import time

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User, Permission, Group
from django.contrib.contenttypes.models import ContentType

from django.test import TestCase

from nose.tools import assert_equal, with_setup, assert_false, eq_, ok_
from nose.plugins.attrib import attr

from teamwork_example.wiki.models import Document

from ..models import Team, Role, Policy
from ..backends import TeamworkBackend

from . import TestCaseBase


class TeamBackendTests(TestCaseBase):

    def setUp(self):
        super(TeamBackendTests, self).setUp()

        self.backend = TeamworkBackend()

    def test_superuser_is_super(self):
        """A superuser should be granted all object permissions"""
        doc = Document.objects.create(name='random_doc_1',
                                      creator=self.users['tester0'])
        obj_perms = Permission.objects.filter(content_type=self.doc_ct).all()
        expected_perms = set([u"%s.%s" % (self.doc_ct.app_label, p.codename)
                              for p in obj_perms])
        result_perms = self.users['admin'].get_all_permissions(doc)
        eq_(expected_perms, result_perms)

    def test_mixed_permissions(self):
        """Policies & teams grant permissions by object to users & groups"""
        anon_user = AnonymousUser()
        auth_user = self.users['tester0']
        role_user = self.users['tester1']
        users_users = (self.users[u] for u in
                       ('tester2', 'tester3'))
        group_users = (self.users[u] for u in
                       ('tester4', 'tester5', 'tester6'))
        owner_user = self.users['tester7']

        expected_anon_perms = set((u'frob', u'xyzzy'))
        expected_auth_perms = set((u'xyzzy', u'hello'))
        expected_role_perms = set((u'frob', u'hello'))
        expected_users_perms = set((u'frob',))
        expected_group_perms = set((u'hello',))
        expected_owner_perms = set((u'add_document_child',))

        team = Team.objects.create(name='general_permissive_team',
                                   founder=self.users['founder0'])

        doc = Document.objects.create(name='general_doc_1',
                                      creator=owner_user,
                                      team=team)

        role1 = Role.objects.create(name='role1', team=team)
        role1.users.add(role_user)
        perms = self.names_to_doc_permissions(expected_role_perms)
        role1.permissions.add(*perms)

        anon_policy = Policy.objects.create(content_object=doc,
                                            anonymous=True)
        perms = self.names_to_doc_permissions(expected_anon_perms)
        anon_policy.permissions.add(*perms)

        auth_policy = Policy.objects.create(content_object=doc,
                                            authenticated=True)
        perms = self.names_to_doc_permissions(expected_auth_perms)
        auth_policy.permissions.add(*perms)

        users_policy = Policy.objects.create(content_object=doc)
        perms = self.names_to_doc_permissions(expected_users_perms)
        users_policy.users.add(*users_users)
        users_policy.permissions.add(*perms)

        group_policy = Policy.objects.create(content_object=doc)
        group = Group.objects.create(name='Honk honk')
        for user in group_users:
            user.groups.add(group)
        group_policy.groups.add(group)
        perms = self.names_to_doc_permissions(expected_group_perms)
        group_policy.permissions.add(*perms)

        owner_policy = Policy.objects.create(content_object=doc,
                                             apply_to_owners=True)
        perms = self.names_to_doc_permissions(expected_owner_perms)
        owner_policy.permissions.add(*perms)

        def assert_perms(expected_perms, user):
            eq_(expected_perms, set(
                n.split('.')[1] for n in
                user.get_all_permissions(doc)))
            for perm in expected_perms:
                user.has_perm(perm, doc)

        assert_perms(expected_anon_perms, anon_user)
        assert_perms(expected_auth_perms, auth_user)
        assert_perms(expected_role_perms, role_user)
        assert_perms(expected_auth_perms.union(expected_owner_perms),
                     owner_user)
        for user in users_users:
            assert_perms(expected_users_perms, user)
        for user in group_users:
            assert_perms(expected_group_perms, user)

    def test_object_logic_permissions(self):
        """Objects can apply custom logic to permissions"""
        u_quux1 = User.objects.create_user(
            'quux1', 'quux1@example.com', 'quux1')
        u_randomguy1 = User.objects.create_user(
            'randomguy1', 'randomguy1@example.com', 'randomguy1')
        doc = Document.objects.create(name='Quuxy')
        ok_(u_quux1.has_perm('wiki.quux', doc))
        ok_(not u_randomguy1.has_perm('wiki.quux', doc))

    def test_parent_permissions(self):
        """Content objects can supply a list of parents for inheritance"""
        docs = []
        for idx in range(0, 7):
            docs.append(Document.objects.create(name=('tree%s' % idx)))

        # Set up document tree:
        #  /- 1 - 4
        # 0 - 2 - 5
        #  \- 3 - 6
        for idx in range(1, 4):
            d = docs[idx]
            d.parent = docs[0]
            d.save()
            d = docs[idx + 3]
            d.parent = docs[idx]
            d.save()

        policy_on_0 = Policy.objects.create(content_object=docs[0],
                                            authenticated=True)
        perms = self.names_to_doc_permissions(('frob',))
        policy_on_0.permissions.add(*perms)

        policy_on_1 = Policy.objects.create(content_object=docs[1],
                                            authenticated=True)
        perms = self.names_to_doc_permissions(('xyzzy',))
        policy_on_1.permissions.add(*perms)

        policy_on_2 = Policy.objects.create(content_object=docs[2],
                                            authenticated=True)
        perms = self.names_to_doc_permissions(('hello',))
        policy_on_2.permissions.add(*perms)

        policy_on_5 = Policy.objects.create(content_object=docs[5],
                                            authenticated=True)
        perms = self.names_to_doc_permissions(('quux',))
        policy_on_5.permissions.add(*perms)

        cases = (
            (u'wiki.frob',),   # 0 has own policy
            (u'wiki.xyzzy',),  # 1 has own policy
            (u'wiki.hello',),  # 2 has own policy
            (u'wiki.frob',),   # 3 inherits from 0
            (u'wiki.xyzzy',),  # 4 inherits from 1
            (u'wiki.quux',),   # 5 has own policy
            (u'wiki.frob',),   # 6 inherits from 0
        )
        user = User.objects.create_user('noob1', 'noob1@example.com', 'noob1')
        for idx in range(0, len(cases)):
            doc = docs[idx]
            case = cases[idx]
            perms = user.get_all_permissions(doc)
            eq_(set(case), perms,
                'Permissions for doc #%s should be %s, were instead %s' %
                (idx, case, perms))

    def test_team_permissions(self):
        """Teams grant permissions to users by role"""
        # FIXME: This test kind of sucks, depends on fixtures set up in another
        # file, and is very hard to follow.
        cases = (
            ('tester0', 'doc1', []),
            ('tester0', 'doc2', []),
            ('tester0', 'doc3', []),
            ('tester1', 'doc1', []),
            ('tester1', 'doc2', []),
            ('tester1', 'doc3', []),
            ('tester2', 'doc1', ['frob']),
            ('tester2', 'doc2', []),
            ('tester2', 'doc3', []),
            ('tester3', 'doc1', []),
            ('tester3', 'doc2', ['hello', 'xyzzy']),
            ('tester3', 'doc3', []),
            ('tester4', 'doc1', []),
            ('tester4', 'doc2', ['hello']),
            ('tester4', 'doc3', ['frob', 'xyzzy']),
            ('tester5', 'doc1', []),
            ('tester5', 'doc2', []),
            ('tester5', 'doc3', ['hello', 'frob', 'xyzzy']),
            ('tester6', 'doc1', []),
            ('tester6', 'doc2', []),
            ('tester6', 'doc3', ['hello', 'frob', 'xyzzy']),
        )
        all_perms = ['wiki.%s' % p[0] for p in (
            'frob', 'xyzzy', 'hello'
        )]
        for user_name, doc_name, perm_names in cases:
            user = self.users[user_name]
            doc = self.docs[doc_name]

            expected = set('wiki.%s' % p for p in perm_names)

            # Try the backend directly, then try it through the user
            result = self.backend.get_all_permissions(user, doc)
            eq_(expected, result)
            result = user.get_all_permissions(doc)
            eq_(expected, result)

            for perm in all_perms:
                hp_expected = perm in expected
                # Try the backend directly, then try it through the user
                hp_result = self.backend.has_perm(user, perm, doc)
                eq_(hp_expected, hp_result)
                hp_result = user.has_perm(perm, doc)
                eq_(hp_expected, hp_result)
