from django.urls import path

from . import views

app_name = "health"

urlpatterns = [
    path("", views.panel, name="panel"),
    path("plan/", views.plan, name="plan"),
    path("targets/", views.targets, name="targets"),
    path("targets/<int:metric_id>/", views.set_target, name="set-target"),
    path("metrics/new/", views.add_metric, name="add-metric"),
    path("log/<slug:slug>/", views.log_recipe, name="log-recipe"),
    path("log/<int:pk>/delete/", views.delete_entry, name="delete-entry"),
    path("readings/new/", views.add_observation, name="add-observation"),
    path("settings/", views.preferences, name="preferences"),

    path("api/day/", views.api_day, name="api-day"),
    path("api/log/", views.api_log, name="api-log"),
    path("api/observation/", views.api_observation, name="api-observation"),
]
