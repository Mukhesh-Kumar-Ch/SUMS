from users.models import User as ProfileUser


def profile_user(request):
    if not request.user.is_authenticated:
        return {"profile_user": None}

    user = ProfileUser.objects.filter(auth_user=request.user).first()
    if user:
        return {"profile_user": user}

    email = getattr(request.user, "email", "")
    if email:
        return {"profile_user": ProfileUser.objects.filter(email__iexact=email).first()}

    return {"profile_user": None}
