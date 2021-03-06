# SYSTEM
from __future__ import absolute_import

# LIBRARIES
from django.db import models
from django.test import TestCase
from django.contrib.contenttypes.models import ContentType
from djangae.contrib.contenttypes.models import SimulatedContentTypeManager


class DummyModel(models.Model):
    pass


class SimulatedContentTypesTests(TestCase):

    def test_contenttypes_patch_is_applied(self):
        self.assertEqual(ContentType.objects.__class__, SimulatedContentTypeManager)

    def test_passing_model_to_simulated_manager_work(self):
        manager = SimulatedContentTypeManager(model=DummyModel)
        self.assertEqual(manager._get_model(), DummyModel)

    def test_get_all_contenttype_objects(self):
        self.assertTrue(len(ContentType.objects.all()) > 0)

    def test_get_for_model(self):
        ct = ContentType.objects.get_for_model(DummyModel)
        self.assertEqual(ct.model, DummyModel._meta.model_name)
        self.assertEqual(ct.app_label, DummyModel._meta.app_label)

    def test_get_by_natural_key(self):
        ct = ContentType.objects.get_by_natural_key(
            DummyModel._meta.app_label, DummyModel._meta.model_name)
        self.assertEqual(ct.model, DummyModel._meta.model_name)
        self.assertEqual(ct.app_label, DummyModel._meta.app_label)

    def test_get_for_id(self):
        ct = ContentType.objects.get_for_model(DummyModel)
        by_id = ContentType.objects.get_for_id(ct.id)
        self.assertEqual(by_id.model, DummyModel._meta.model_name)
        self.assertEqual(by_id.app_label, DummyModel._meta.app_label)

    def test_create_contenttype(self):
        ct = ContentType.objects.create(app_label='test', model='test')
        self.assertEqual(ct.app_label, 'test')
        self.assertEqual(ct.model, 'test')
        self.assertIsNotNone(ct.pk)

    def test_get_or_create_contenttype(self):
        ct, created = ContentType.objects.get_or_create(
            app_label=DummyModel._meta.app_label,
            model=DummyModel._meta.model_name
        )

        self.assertEqual(ct.model, DummyModel._meta.model_name)
        self.assertEqual(ct.app_label, DummyModel._meta.app_label)
        self.assertFalse(created)

        ct, created = ContentType.objects.get_or_create(
            app_label=DummyModel._meta.app_label,
            model='different_model'
        )

        self.assertEqual(ct.model, 'different_model')
        self.assertEqual(ct.app_label, DummyModel._meta.app_label)
        self.assertTrue(created)

    def test_filter_contenttypes(self):
        original_count = len(ContentType.objects.all())
        self.assertTrue(original_count > len(ContentType.objects.filter(app_label=DummyModel._meta.app_label)))
