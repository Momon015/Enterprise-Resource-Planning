from django.forms import ModelForm
from django import forms

from Supplier.models import Material, Supplier
from core.models import Category

from core.utils.forms import mark_required
from core.utils.images import process_uploaded_image

# Create your forms here.

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
        fields = ['name', 'price', 'quantity', 'unit', 'supplier', 'piece_per_unit']

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

        mark_required(self)


class SupplierForm(ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'image', 'email', 'contact_number']

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Coca-Cola Distributor',
                'autocomplete': 'off',
                'class': 'form-control',
            }),
            'image': forms.FileInput(attrs={      # ← plain FileInput
                'accept': 'image/*',
                'class': 'form-control',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control', 
                'placeholder': 'e.g. orders@vendor.com', 
                'autocomplete': 'off'
            }),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'e.g. 09171234567', 
                'inputmode': 'numeric', 
                'autocomplete': 'off'
            }),
        }
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].required = True

        mark_required(self)

    def clean_name(self):
        name = self.cleaned_data.get('name').strip()
        if name.lower() == 'no supplier':
            raise forms.ValidationError('"No Supplier" is a reserved name. Please choose a different one.')
        return name

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            return image
        if not hasattr(image, 'content_type'):
            return image
        
        # Capture original filename BEFORE the helper renames it to a uuid
        self.instance.image_original_name = image.name
        
        return process_uploaded_image(image)
    
class SupplierFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)


class PresetFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)
