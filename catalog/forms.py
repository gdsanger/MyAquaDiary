from django import forms
from django.forms import inlineformset_factory

from .models import CatalogAnimal, CatalogAnimalImage


class CatalogAnimalForm(forms.ModelForm):
    class Meta:
        model = CatalogAnimal
        exclude = ["slug", "created_by"]


CatalogAnimalImageFormSet = inlineformset_factory(
    CatalogAnimal,
    CatalogAnimalImage,
    fields=["image", "caption", "sex", "credit", "is_primary", "position"],
    extra=1,
    can_delete=True,
)
