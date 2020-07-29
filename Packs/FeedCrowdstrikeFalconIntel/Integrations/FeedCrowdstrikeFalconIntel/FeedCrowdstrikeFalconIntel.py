import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *

# IMPORTS
import requests
from typing import List, Tuple

# Disable insecure warnings
requests.packages.urllib3.disable_warnings()


class Client(BaseClient):

    def __init__(self, params):
        self.client_id = params.get('client_id')
        self.client_secret = params.get('client_secret')
        super().__init__(base_url="https://api.crowdstrike.com/", verify=not params.get('insecure', False),
                         ok_codes=tuple(), proxy=params.get('proxy', False))
        self._token = self._generate_token()
        self._headers = {'Authorization': 'Bearer ' + self._token}

    @staticmethod
    def _error_handler(error_entry: dict) -> str:
        errors = error_entry.get("errors", [])
        return '\n' + '\n'.join(f"{error['code']}: {error['message']}" for error in errors)

    def http_request(self, method, url_suffix, full_url=None, headers=None, json_data=None, params=None, data=None,
                     files=None, timeout=10, ok_codes=None, return_empty_response=False, auth=None):

        return super()._http_request(method=method, url_suffix=url_suffix, full_url=full_url, headers=headers,
                                     json_data=json_data, params=params, data=data, files=files, timeout=timeout,
                                     ok_codes=ok_codes, return_empty_response=return_empty_response, auth=auth)

    def _generate_token(self) -> str:
        """Generate an Access token using the user name and password
        :return: valid token
        """
        body = {
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }
        token_res = self.http_request('POST', '/oauth2/token', data=body, auth=(self.client_id, self.client_secret))
        return token_res.get('access_token')

    def check_quota_status(self) -> dict:
        url_suffix = "/intel/combined/actors/v1"
        return self.http_request('GET', url_suffix)

    # def daily_http_request(self) -> list:
    #     """The HTTP request for daily feeds.
    #     Returns:
    #         list. A list of indicators fetched from the feed.
    #     """
    #     headers = {
    #         "apiKey": self.api_key,
    #         'Content-Type': "application/json"
    #     }
    #
    #     res = requests.request(
    #         method="GET",
    #         url='https://autofocus.paloaltonetworks.com/api/v1.0/output/threatFeedResult',
    #         verify=self.verify,
    #         headers=headers
    #     )
    #     res.raise_for_status()
    #     indicator_list = res.text.split('\n')
    #     return indicator_list

    def create_indicators_from_response(self, response, feed_tags: list) -> list:
        """
        Creates a list of indicators from a given response
        Args:
            response: List of dict that represent the response from the api
            feed_tags: The indicator tags
        Returns:
            List of indicators with the correct indicator type.
        """
        parsed_indicators = []  # type:List
        indicator = {}
        for actor in response['resources']:
            if actor:
                indicator = {
                    "type": 'Actor',
                    "value": actor['name'],
                    "rawJSON": {
                        'type': 'Actor',
                        'value': actor['name'],
                        'service': 'List Actors Feed'
                    },
                    'fields': {'service': 'Daily Threat Feed', 'tags': feed_tags}
                }
                indicator['rawJSON'].update(actor)
            parsed_indicators.append(indicator)

        return parsed_indicators

    def build_iterator(self, feed_tags: List, limit=None, offset=None):
        """Builds a list of indicators.
        Returns:
            list. A list of JSON objects representing indicators fetched from a feed.
        """
        response = self.check_quota_status()
        parsed_indicators = self.create_indicators_from_response(response, feed_tags)  # list of dict of indicators

        # for get_indicator_command only
        if limit:
            parsed_indicators = parsed_indicators[int(offset): int(offset) + int(limit)]
        return parsed_indicators


def module_test_command(client: Client, args: dict, feed_tags: list):
    """
    Returning 'ok' indicates that the integration works like it is supposed to. Connection to the service is successful.

    Args:
        client(Client): Autofocus Feed client
        args(Dict): The instance parameters
        feed_tags: The indicator tags

    Returns:
        'ok' if test passed, anything else will fail the test.
    """
    try:
        client.build_iterator(argToList(demisto.params().get('feedTags')), 1, 0)
    except Exception:
        raise Exception("Could not fetch Daily Threat Feed\n"
                        "\nCheck your API key and your connection to AutoFocus.")
    return 'ok', {}, {}


def get_indicators_command(client: Client, args: dict, feed_tags: list) -> Tuple[str, dict, list]:
    """Initiate a single fetch-indicators

    Args:
        client(Client): The AutoFocus Client.
        args(dict): Command arguments.
        feed_tags: The indicator tags
    Returns:
        str, dict, list. the markdown table, context JSON and list of indicators
    """
    offset = int(args.get('offset', 0))
    limit = int(args.get('limit', 100))

    indicators = fetch_indicators_command(client, feed_tags, limit, offset)

    hr_indicators = []
    for indicator in indicators:
        hr_indicators.append({
            'Value': indicator.get('value'),
            'Type': indicator.get('type'),
            'rawJSON': indicator.get('rawJSON'),
            'fields': indicator.get('fields'),
        })

    human_readable = tableToMarkdown("Indicators from Crowdstrike:", hr_indicators,
                                     headers=['Value', 'Type', 'rawJSON', 'fields'], removeNull=True)

    if args.get('limit'):
        human_readable = human_readable + f"\nTo bring the next batch of indicators " \
                                          f"run:\n!crowdstrike-falcon-intel-get-indicators " \
                                          f"limit={args.get('limit')} " \
                                          f"offset={int(str(args.get('limit'))) + int(str(args.get('offset')))}"

    return human_readable, {}, indicators


def fetch_indicators_command(client: Client, feed_tags: List, limit=None, offset=None) -> list:
    """Fetch-indicators command from AutoFocus Feeds

    Args:
        client(Client): AutoFocus Feed client.
        feed_tags: The indicator tags
        limit: limit the amount of indicators fetched.
        offset: the index of the first index to fetch.

    Returns:
        list. List of indicators.
    """
    indicators = client.build_iterator(feed_tags, limit, offset)

    return indicators


def main():
    params = demisto.params()
    feed_tags = argToList(params.get('feedTags'))
    client = Client(params)

    command = demisto.command()
    demisto.info(f'Command being called is {command}')
    # Switch case
    commands = {
        'test-module': module_test_command,
        'crowdstrike-falcon-intel-get-indicators': get_indicators_command
    }
    try:
        if demisto.command() == 'fetch-indicators':
            indicators = fetch_indicators_command(client, feed_tags)
            # we submit the indicators in batches
            for b in batch(indicators, batch_size=2000):
                demisto.createIndicators(b)
        else:
            readable_output, outputs, raw_response = commands[command](client, demisto.args(),
                                                                       feed_tags)  # type: ignore
            return_outputs(readable_output, outputs, raw_response)
    except Exception as e:
        raise Exception(f'Error in Crowdstrike falcon intel  Integration [{e}]')


if __name__ == '__builtin__' or __name__ == 'builtins':
    main()
