from django.db import models


class VideoCategory(models.TextChoices):
    DRAMA = "drama", "Drama"
    ROMANCE = "romance", "Romance"
    ACTION = "action", "Action"
    DOCUMENTARY = "documentary", "Documentary"
    COMEDY = "comedy", "Comedy"
