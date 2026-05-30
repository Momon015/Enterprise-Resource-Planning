from django.forms import ModelForm
from django import forms

from Supplier.models import Material, Supplier
from core.models import Category


class MaterialFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)

        self.fields['category'].queryset = Category.objects.filter(
            category_type='material', business=business
        )


class MaterialForm(ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'price', 'category', 'quantity', 'unit', 'supplier', 'piece_per_unit']

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Coke 1.5L',
                'autocomplete': 'off',
            }),
            'price': forms.NumberInput(attrs={
                'min': '0',
                'step': '0.01',
                'inputmode': 'decimal',
                'placeholder': '0.00',
            }),
            'quantity': forms.NumberInput(attrs={
                'min': '1',
                'inputmode': 'numeric',
            }),
            'piece_per_unit': forms.NumberInput(attrs={
                'min': '1',
                'inputmode': 'numeric',
                'placeholder': 'e.g. 24 for a case of bottles',
            }),
        }

    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)

        # Defaults on create
        if not self.instance.pk:
            self.initial.setdefault('quantity', 1)
            self.initial.setdefault('piece_per_unit', 1)

        # Preserve price format on edit
        if self.instance.pk and self.instance.price:
            self.initial['price'] = f"{self.instance.price:.2f}"

        # Category dropdown scoped to this business
        self.fields['category'].queryset = Category.objects.filter(
            category_type='material', business=business
        )
        self.fields['category'].empty_label = None
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()

        # Supplier dropdown scoped to this business
        self.fields['supplier'].queryset = Supplier.objects.filter(business=business)
        self.fields['supplier'].empty_label = None

        # Friendlier labels
        self.fields['piece_per_unit'].label = 'Pieces per Unit'
        self.fields['price'].label = 'Unit Price'

        # Apply form-control without clobbering existing widget attrs
        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-control').strip()


class SupplierForm(ModelForm):
    class Meta:
        model = Supplier
        fields = ['name']

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Coca-Cola Distributor',
                'autocomplete': 'off',
                'class': 'form-control',
            }),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name').strip()
        if name.lower() == 'no supplier':
            raise forms.ValidationError('"No Supplier" is a reserved name. Please choose a different one.')
        return name


class SupplierFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)


class PresetFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)
