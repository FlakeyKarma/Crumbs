from django.urls import path

from . import views

app_name = "recipes"

urlpatterns = [
    path("", views.RecipeListView.as_view(), name="list"),
    path("new/", views.RecipeCreateView.as_view(), name="create"),
    path("tags/", views.TagListView.as_view(), name="tags"),
    path("tags/<slug:slug>/", views.RecipeListView.as_view(), name="tag"),

    path("r/<slug:slug>/", views.RecipeDetailView.as_view(), name="detail"),
    path("r/<slug:slug>/edit/", views.RecipeUpdateView.as_view(), name="edit"),
    path("r/<slug:slug>/delete/", views.RecipeDeleteView.as_view(), name="delete"),
    path("r/<slug:slug>/cooked/", views.mark_cooked, name="mark-cooked"),
    path("r/<slug:slug>/notes/new/", views.note_create, name="note-create"),

    path("notes/<int:pk>/edit/", views.note_edit, name="note-edit"),
    path("notes/<int:pk>/delete/", views.note_delete, name="note-delete"),
    path("notes/<int:pk>/sharing/", views.note_share, name="note-share"),

    path("appearance/theme/", views.set_theme, name="set-theme"),
    path("appearance/mode/", views.set_mode, name="set-mode"),

    path("settings/", views.settings_home, name="settings"),
    path("settings/appearance/", views.set_default_appearance, name="set-default-appearance"),
    path("settings/themes/new/", views.theme_create, name="theme-create"),
    path("settings/themes/<slug:key>/", views.theme_edit, name="theme-edit"),
    path("settings/themes/<slug:key>/use/", views.theme_activate, name="theme-activate"),
    path("settings/themes/<slug:key>/delete/", views.theme_delete, name="theme-delete"),
]
