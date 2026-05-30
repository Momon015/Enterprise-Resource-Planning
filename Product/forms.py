from django.forms import ModelForm
from django import forms

from Product.models import Product, ProductPreset, ProductPresetItem
from core.models import Category


class ProductForm(ModelForm):

    class Meta:
        model = Product
        fields = ['name', 'description', 'sku', 'barcode',
                  'prepared_quantity', 'selling_price', 'category']

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Coke 1.5L',
                'autocomplete': 'off',
            }),
            'sku': forms.TextInput(attrs={
                'placeholder': 'Leave blank — we\'ll generate PRD-0001',
                'autocomplete': 'off',
            }),
            'barcode': forms.TextInput(attrs={
                'placeholder': 'e.g. 4801234567890',
                'autocomplete': 'off',
                'inputmode': 'numeric',
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Optional notes — size, variant, supplier hint, etc.',
            }),
            'selling_price': forms.NumberInput(attrs={
                'min': '0',
                'step': '0.01',
                'inputmode': 'decimal',
            }),
            'prepared_quantity': forms.NumberInput(attrs={
                'min': '1',
                'inputmode': 'numeric',
            }),
        }

    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        # if not self.instance.cost_price:
        #     self.initial['cost_price'] = '0.00'
        
        # if self.instance.cost_price:
        #     self.initial['cost_price'] = f"{self.instance.cost_price:.2f}"

        # Default selling price to 0.00 on create, preserve format on edit
        if not self.instance.selling_price:
            self.initial['selling_price'] = '0.00'
        else:
            self.initial['selling_price'] = f"{self.instance.selling_price:.2f}"

        # Default quantity to 1 on create
        if not self.instance.pk:
            self.initial['prepared_quantity'] = 1

        # Category dropdown scoped to this business
        self.fields['category'].queryset = Category.objects.filter(
            category_type='product', business=business
        )
        self.fields['category'].empty_label = None
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()

        # Friendlier labels
        self.fields['selling_price'].label = 'Unit Price'
        self.fields['prepared_quantity'].label = 'Quantity'
        self.fields['sku'].label = 'SKU'
        self.fields['barcode'].label = 'Barcode'
        self.fields['sku'].required = False
        self.fields['barcode'].required = False
        
        # Apply form-control class without nuking existing widget attrs
        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-control').strip()


class ProductFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.filter(
            category_type='product', business=business
        )


class ProductPresetFilterForm(forms.Form):
    search = forms.CharField(required=False)
