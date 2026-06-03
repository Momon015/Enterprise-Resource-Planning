from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from core.utils.owner import get_business_for_user
from chatbot.commands import parse_and_execute, LANG_KEY, DEFAULT_LANG

# Create your views here.


@login_required(login_url='login')
def ask_chatbot(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    query = request.GET.get('q', '').strip()
    response = parse_and_execute(request, query, business) if query else ''
    
    current_lang = request.session.get(LANG_KEY, DEFAULT_LANG)
    
    
    context = {
        'section': 'chatbot',
        'query': query,
        'response': response,
        'current_lang': current_lang,
    }
    return render(request, 'chatbot/ask_chatbot.html', context)