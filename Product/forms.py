from django.forms import ModelForm
from django import forms

from Product.models import Product, ProductPreset, ProductPresetItem

from core.models import Category

# Create your forms here.
        
class ProductForm(ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'description', 'prepared_quantity', 'selling_price', 'category']
    
        widgets = {
            'cost_price': forms.NumberInput(attrs={
                'min': '0',
                'value': '0.00',
                'step': '0.01'
            }),
            
            'selling_price': forms.NumberInput(attrs={
                'min': '0',
                'value': '0.00',
                'step': '0.01'
            }),
            
            'prepared_quantity': forms.NumberInput(attrs={
                'min': '1',
                'value': '1',
            })
            
        }
            
    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)
        
        # if not self.instance.cost_price:
        #     self.initial['cost_price'] = '0.00'
        
        if not self.instance.selling_price:
            self.initial['selling_price'] = '0.00'
                
        # if self.instance.cost_price:
        #     self.initial['cost_price'] = f"{self.instance.cost_price:.2f}"
        
        if self.instance.selling_price:
            self.initial['selling_price'] = f"{self.instance.selling_price:.2f}"
        
        def category_label(obj):
            return obj.name.title()
        
        self.fields['category'].queryset = Category.objects.filter(category_type='product')
        self.fields['category'].empty_label = None
        self.fields['category'].label_from_instance = category_label # or lambda obj: obj.name.title()

        self.fields['selling_price'].label = 'Unit Price'
            # self.fields['cost_price'].label = 'Unit Cost'
  
        self.fields['prepared_quantity'].label = 'Quantity'
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

class ProductFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.filter(category_type='product')
       

        
        