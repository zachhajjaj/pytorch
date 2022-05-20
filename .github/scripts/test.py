from trymerge import _fetch_url, fetch_json
owner = 'pytorch'
name = 'pytorch'
commit = '53e0d7a3bae621ef0d07cfd44957736e9149dd8e'
checks = _fetch_url(f'https://api.github.com/repos/{owner}/{name}/commits/{commit}/check-runs')
checks2 = fetch_json(f'https://api.github.com/repos/{owner}/{name}/commits/{commit}/check-runs')

print(checks)
