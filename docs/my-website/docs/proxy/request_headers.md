# Request Headers

Special headers that are supported by LiteLLM.

## LiteLLM Headers

`x-litellm-timeout` Optional[float]: The timeout for the request in seconds.

`x-litellm-enable-message-redaction`: Optional[bool]: Don't log the message content to logging integrations. Just track spend. [Learn More](./logging#redact-messages-response-content)

`x-litellm-tags`: Optional[str]: A comma separated list (e.g. `tag1,tag2,tag3`) of tags to use for [tag-based routing](./tag_routing) **OR** [spend-tracking](./enterprise.md#tracking-spend-for-custom-tags).

`x-litellm-num-retries`: Optional[int]: The number of retries for the request.

## Anthropic Headers

`anthropic-version` Optional[str]: The version of the Anthropic API to use.  
`anthropic-beta` Optional[str]: The beta version of the Anthropic API to use.
    - For `/v1/messages` endpoint, this will always be forward the header to the underlying model.
    - For `/chat/completions` endpoint, this will only be forwarded if `forward_client_headers_to_llm_api` is true.

## OpenAI Headers

`openai-organization` Optional[str]: The organization to use for the OpenAI API. (currently needs to be enabled via `general_settings::forward_openai_org_id: true`)



