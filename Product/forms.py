from django.forms import ModelForm
from django import forms

from Product.models import Product, ProductPreset, ProductPresetItem
from core.models import Category

from core.utils.images import process_uploaded_image

class ProductForm(ModelForm):

    class Meta:
        model = Product
        fields = ['name', 'description', 'image', 'barcode',
                  'prepared_quantity', 'selling_price', 'category',
                  'low_stock_threshold', 'high_stock_threshold', 'target_margin', 'cost_price']

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Coke 1.5L',
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
            
            'image': forms.FileInput(attrs={
                'accept': 'image/*',
                'class': 'form-control',
            }),
            
            'low_stock_threshold': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
            }),
            
            'high_stock_threshold': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
            }),
            
            'target_margin': forms.NumberInput(attrs={
                'min': '10', 'max': '90', 'inputmode': 'numeric',
                'placeholder': 'eg. 30'
            }),

            # 'sku': forms.TextInput(attrs={
            #     'placeholder': 'Leave blank — we\'ll generate PRD-0001',
            #     'autocomplete': 'off',
            # }),
        }
        
    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            return image  # field is optional
        
        # If image is an existing stored file (edit form, user didn't replace it),
        # it won't have a content_type attribute. Skip processing.
        if not hasattr(image, 'content_type'):
            return image
        
        # Capture original filename BEFORE the helper renames it to a uuid
        self.instance.image_original_name = image.name

        return process_uploaded_image(image)
    
    def clean_prepared_quantity(self):
        qty = self.cleaned_data.get('prepared_quantity')
        if self.instance and self.instance.pk and self.instance.material:
            # Material-linked: ignore submitted value, keep current
            return self.instance.prepared_quantity
        return qty
    
    def clean_cost_price(self):
        cost = self.cleaned_data.get('cost_price')
        if self.instance and self.instance.pk and self.instance.material:
            return self.instance.cost_price  # linked: keep material-derived cost
        return cost or 0



    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Owner-only target margin (del for staff → not rendered AND not accepted on POST)
        if getattr(user, 'role', None) != 'owner':
            self.fields.pop('target_margin', None)
        else:
            self.fields['target_margin'].required = False
            self.fields['target_margin'].label = 'Target margin %'
            self.fields['target_margin'].widget.attrs['placeholder'] = str(self.instance.effective_target_margin)
            
        self.fields['cost_price'].label = 'Unit Cost'
        self.fields['cost_price'].required = False
        if not self.instance.cost_price:
            self.initial['cost_price'] = '0.00'
        else:
            self.initial['cost_price'] = f"{self.instance.cost_price:.2f}"

        # Material-linked products: cost is managed by the material's stock → lock it
        if self.instance and self.instance.pk and self.instance.material:
            self.fields['cost_price'].disabled = True

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
        self.fields['cost_price'].label = 'Unit Cost'
        
        # self.fields['sku'].label = 'SKU'
        # self.fields['sku'].required = False
        
        self.fields['barcode'].label = 'Barcode'
        self.fields['barcode'].required = False
        
        self.fields['low_stock_threshold'].label = 'Low stock at'
        self.fields['high_stock_threshold'].label = 'High stock at'
        
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


class ServiceForm(ModelForm):
    """Minimal form for service fees (xerox, GCash, bills payment).
    A service is a Product with is_service=True — no stock, no cost, no category, no margin."""

    class Meta:
        model = Product
        fields = ['name', 'selling_price', 'description', 'image']
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Gcash Cash In/Out',
                'autocomplete': 'off',
            }),
            'selling_price': forms.NumberInput(attrs={
                'min': '0', 'step': '0.01', 'inputmode': 'decimal',
            }),
            'description': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Optional — what this fee covers',
            }),
            'image': forms.FileInput(attrs={
                'accept': 'image/*',
                'class': 'form-control',
            }),
        }

    def __init__(self, *args, **kwargs):
        # accept business/user for call-site parity with ProductForm; neither is needed
        # (no category scoping, no owner gating on services)
        kwargs.pop('business', None)
        kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        self.fields['image'].label = 'Logo / Image'
        self.fields['image'].required = False


        self.fields['name'].label = 'Service name'
        self.fields['selling_price'].label = 'Fee'
        self.fields['description'].label = 'Description'
        self.fields['description'].required = False

        if not self.instance.selling_price:
            self.initial['selling_price'] = '0.00'
        else:
            self.initial['selling_price'] = f"{self.instance.selling_price:.2f}"

        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-control').strip()

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.is_service = True
        obj.cost_price = 0
        if obj.prepared_quantity is None:
            obj.prepared_quantity = 0   # services bypass stock, but the field is non-null
        if commit:
            obj.save()
        return obj
    
    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            return image  # optional
        if not hasattr(image, 'content_type'):
            return image  # existing stored file on edit, not re-uploaded
        self.instance.image_original_name = image.name
        return process_uploaded_image(image)

