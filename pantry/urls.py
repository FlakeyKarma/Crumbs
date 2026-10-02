from django.urls import path

from . import views

app_name = "pantry"

urlpatterns = [
    path("", views.pantry_home, name="home"),
    path("cook/", views.what_can_i_cook, name="cook"),
    path("settings/", views.credentials, name="credentials"),

    path("foods/", views.food_search, name="search"),
    path("foods/new/", views.food_create, name="entry-create"),
    path("foods/import/", views.import_foods, name="import"),
    path("foods/<int:pk>/edit/", views.food_edit, name="entry-edit"),
    path("foods/<int:pk>/", views.food_detail, name="entry"),
    path("foods/<int:pk>/stock/", views.stock_add, name="stock-add"),
    path("foods/<int:pk>/portions/", views.portion_add, name="portion-add"),

    path("lots/<int:pk>/move/", views.stock_move, name="stock-move"),

    path("references/", views.references, name="references"),
    path("references/<int:pk>/", views.reference_edit, name="reference-edit"),
    path("references/<int:pk>/attach/", views.reference_attach, name="reference-attach"),
    path("references/<int:pk>/patterns/", views.reference_pattern_add, name="reference-pattern-add"),
    path("reference-patterns/<int:pk>/", views.reference_pattern_edit, name="reference-pattern-edit"),
    path("reference-patterns/<int:pk>/remove/", views.reference_pattern_remove, name="reference-pattern-remove"),
    path("portions/search/", views.meal_food_search, name="meal-food-search"),
    path("references/<int:pk>/options/", views.reference_options, name="reference-options"),

    path("terms/<int:ingredient_id>/", views.choose_default, name="choose-default"),

    # JSON, for the phone client that scans barcodes.
    path("api/lookup/", views.api_lookup, name="api-lookup"),
    path("api/import/", views.api_import, name="api-import"),
    path("api/stock/", views.api_stock, name="api-stock"),
]
