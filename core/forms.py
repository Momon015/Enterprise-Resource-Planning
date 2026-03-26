from django.forms import ModelForm
from django import forms

from core.models import Category

# Create your forms here.

class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'category_type']
        
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['category_type'].empty_label = None
        self.fields['category_type'].choices = [('item', 'Item'), ('expense', 'Expense'), ('product', 'Product')]

        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

class CategoryFilterForm(forms.Form):
    search = forms.CharField(required=False)