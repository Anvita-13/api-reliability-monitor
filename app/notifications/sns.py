from app.notifications.base import AlertMessage


class SnsNotifier:
    """Publish alerts to an Amazon SNS topic (email/SMS/etc. subscribers get them).

    Uses the standard AWS credential chain: on EC2 that is the instance's IAM role, which
    only needs ``sns:Publish`` on the topic. No access keys are stored anywhere.
    """

    def __init__(self, topic_arn: str, region: str | None = None, client=None):
        if client is None:
            import boto3  # imported lazily so local development does not need AWS

            client = boto3.client("sns", region_name=region)
        self.topic_arn = topic_arn
        self.client = client

    def send(self, message: AlertMessage) -> None:
        self.client.publish(TopicArn=self.topic_arn, Subject=message.subject, Message=message.body)
