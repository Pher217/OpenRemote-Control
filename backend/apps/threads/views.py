from rest_framework import decorators, response, viewsets

from apps.threads.models import Message, Thread
from apps.threads.serializers import MessageSerializer, ThreadSerializer


class ThreadViewSet(viewsets.ModelViewSet):
    queryset = Thread.objects.all()
    serializer_class = ThreadSerializer

    @decorators.action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        thread = self.get_object()
        if request.method == "POST":
            data = {**request.data, "thread": thread.id}
            serializer = MessageSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return response.Response(serializer.data, status=201)
        queryset = thread.messages.all()
        serializer = MessageSerializer(queryset, many=True)
        return response.Response(serializer.data)


class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all()
    serializer_class = MessageSerializer
