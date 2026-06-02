from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.http import JsonResponse

from core.utils.owner import get_business_for_user
from .models import ActivityEvent

# Create your views here.

@login_required(login_url='login')
def view_all_activity(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    events = ActivityEvent.objects.filter(business=business)

    category = request.GET.get('category')
    if category:
        events = events.filter(verb__startswith=f'{category}.')

    paginator = Paginator(events, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'activity/view_all_activity.html', {
        'page_obj': page_obj,
        'section': 'activity',
        'active_category': category,
    })


@login_required(login_url='login')
@require_POST
def mark_all_read(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    ActivityEvent.objects.filter(
        business=business, is_important=True, is_read=False
    ).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@require_POST
def mark_one_read(request, business_slug, event_id):
    business = get_business_for_user(request.user, business_slug)
    ActivityEvent.objects.filter(
        business=business, pk=event_id
    ).update(is_read=True)
    return JsonResponse({'ok': True})
