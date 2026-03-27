from functools import wraps
from django.shortcuts import redirect, render
from django.contrib import messages

# utils/owner.py
def get_owner(user):
    if user.role == 'owner':
        return user
    return user.owner

def permission_required(action):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.role == 'staff':
                if action == 'delete':
                    return render(request, 'core/no_permission.html', status=403)
                if action == 'owner_only':
                    return render(request, 'core/no_access.html', status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


