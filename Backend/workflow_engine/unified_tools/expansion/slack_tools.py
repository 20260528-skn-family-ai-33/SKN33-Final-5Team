"""Register the scoped Slack delivery operation with the workflow runtime."""


def register_slack_tools(tool, invoke_connector, registry):
    @tool("slack.post_message", "Post a bound text result to an explicit Slack conversation ID.")
    def slack_post_message(channel_id, text, context):
        return invoke_connector("slack", "slack.post_message", {"channel_id": channel_id, "text": text}, context)

    registry["slack.post_message"].update(provider_required=True, provider="slack", side_effect=True)
