# reports/urls.py
from django.urls import path
from .views import edit_tests
from . import views

urlpatterns = [
    path('', views.build_list, name='build_list'),
    path('build/<int:build_id>/', views.build_detail, name='build_detail'),
    path('test/<str:test_name>/<str:architecture>/', views.test_history, name='test_history'),
    path('reports/build/<int:build_id>/edit-tests/', edit_tests, name='edit_tests'),
    path('test/<int:test_case_id>/', views.test_detail, name='test_detail'),
    path('testrun/<int:testrun_id>/edit/', views.edit_testrun_statuses, name='edit_testrun_statuses'),
]