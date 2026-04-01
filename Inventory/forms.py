from django.forms import ModelForm
from django import forms

from Inventory.models import Stock
from Supplier.models import Material

from core.models import Category

# Create your forms here.

class StockFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
        
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        qs = Category.objects.filter(category_type='item', user=user)
        self.fields['category'].queryset = qs
        
class StockForm(ModelForm):
    class Meta:
        model = Material
        fields = ['category']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['category'].empty_label = None
        self.fields['category'].queryset = Category.objects.filter(category_type='item')
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            

            

            


            
            