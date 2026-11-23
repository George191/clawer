export const sdkLanguages = { typescript: 'TypeScript', python: 'Python', java: 'Java', c: 'C' } as const;
export type SdkLanguage = keyof typeof sdkLanguages;
export const sdkRequirements: Record<SdkLanguage, string> = {
  typescript: 'Browser fetch or Node.js 18+. The client returns decoded JSON and rejects integers outside the safe JavaScript number range.',
  python: 'Python 3.10+ standard library. The client returns the decoded JSON response.',
  java: 'Java 11+ standard library. Compile PublicationClient.java; the client returns JSON text.',
  c: 'C99 and libcurl. Compile as a library object: cc -std=c99 -c publication_client.c $(pkg-config --cflags libcurl). Link your program with libcurl. The caller frees the returned JSON buffer. Initialize libcurl once before using the client.',
};
export const sdkFilename = (language: SdkLanguage) => ({ typescript: 'publication-client.ts', python: 'publication_client.py', java: 'PublicationClient.java', c: 'publication_client.c' })[language];
export const sdkUsage: Record<SdkLanguage, string> = {
  typescript: 'import { PublicationClient } from "./publication-client.js";\nconst api = new PublicationClient(process.env.API_KEY!);\nconsole.log(await api.readPage(50, 0));',
  python: 'import os\nfrom publication_client import PublicationClient\napi = PublicationClient(os.environ["API_KEY"])\nprint(api.read_page(50, 0))',
  java: 'PublicationClient api = new PublicationClient(System.getenv("API_KEY"));\nSystem.out.println(api.readPage(50, 0)); // In a method declaring throws Exception',
  c: '/* Include curl/curl.h and declare publication_read_page before this call. */\ncurl_global_init(CURL_GLOBAL_DEFAULT);\nchar *json = NULL;\nCURLcode rc = publication_read_page(getenv("API_KEY"), 50, 0, &json);\nif (rc == CURLE_OK) { puts(json); free(json); }\nelse { fprintf(stderr, "%s\\n", curl_easy_strerror(rc)); }\ncurl_global_cleanup();',
};

export function generatePublicationSdk(endpoint: string, language: SdkLanguage, maxLimit = 500, maxOffset = 1000000): string {
  const url = new URL(endpoint);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('A credential-free HTTP API endpoint is required.');
  const quoted = JSON.stringify(url.href);
  if (language === 'python') return `"""Generated read-only client. Supply an issued API key at runtime; never share it."""
import json
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class PublicationClient:
    def __init__(self, api_key):
        if not api_key or "\\r" in api_key or "\\n" in api_key:
            raise ValueError("An API key without newlines is required")
        self.api_key = api_key

    def read_page(self, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= ${maxLimit} or type(offset) is not int or not 0 <= offset <= ${maxOffset}:
            raise ValueError("Invalid page bounds")
        query = urlencode({"limit": limit, "offset": offset})
        request = Request(${quoted} + "?" + query,
                          headers={"X-API-Key": self.api_key, "Accept": "application/json"})
        # HTTPError is raised for non-success responses. Do not log the request headers.
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
`;
  if (language === 'java') return `// Generated read-only client. Supply an issued API key at runtime.
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

public final class PublicationClient {
    private final String apiKey;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(30)).build();
    public PublicationClient(String apiKey) {
        if (apiKey == null || apiKey.isEmpty() || apiKey.contains("\\r") || apiKey.contains("\\n"))
            throw new IllegalArgumentException("An API key without newlines is required");
        this.apiKey = apiKey;
    }
    public String readPage(int limit, int offset) throws IOException, InterruptedException {
        if (limit < 1 || limit > ${maxLimit} || offset < 0 || offset > ${maxOffset})
            throw new IllegalArgumentException("Invalid page bounds");
        HttpRequest request = HttpRequest.newBuilder(URI.create(${quoted} + "?limit=" + limit + "&offset=" + offset))
            .timeout(Duration.ofSeconds(30)).header("X-API-Key", apiKey).header("Accept", "application/json").GET().build();
        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() < 200 || response.statusCode() >= 300)
            throw new IOException("Publication HTTP " + response.statusCode() + ": " + response.body());
        return response.body();
    }
}
`;
  if (language === 'c') return `/* Generated read-only client. Requires libcurl; no credentials are embedded.
 * Initialize curl_global_init(CURL_GLOBAL_DEFAULT) once before use.
 * Example: char *json = NULL; CURLcode rc = publication_read_page(getenv("API_KEY"), 50, 0, &json);
 * On success json contains the response text; free(json) after use. Call curl_global_cleanup() at shutdown.
 * Compile as a library object: cc -std=c99 -c publication_client.c $(pkg-config --cflags libcurl)
 * Link your application with: $(pkg-config --libs libcurl)
 */
#include <curl/curl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct publication_buffer { char *data; size_t length; };
static size_t publication_append(void *ptr, size_t size, size_t count, void *userdata) {
    struct publication_buffer *buffer = (struct publication_buffer *)userdata;
    size_t bytes;
    char *next;
    if (size && count > (size_t)-1 / size) return 0;
    bytes = size * count;
    if (bytes > 64 * 1024 * 1024 || buffer->length > 64 * 1024 * 1024 - bytes) return 0;
    next = (char *)realloc(buffer->data, buffer->length + bytes + 1);
    if (!next) return 0;
    buffer->data = next;
    memcpy(next + buffer->length, ptr, bytes);
    buffer->length += bytes;
    next[buffer->length] = '\\0';
    return bytes;
}
CURLcode publication_read_page(const char *api_key, int limit, int offset, char **json) {
    CURL *curl;
    CURLcode result;
    long status = 0;
    struct curl_slist *headers = NULL, *next;
    struct publication_buffer buffer = {NULL, 0};
    char authorization[280];
    char url[${url.href.length + 80}];
    if (!json) return CURLE_BAD_FUNCTION_ARGUMENT;
    *json = NULL;
    if (!api_key || !*api_key || strlen(api_key) > 256 || strpbrk(api_key, "\\r\\n") || limit < 1 || limit > ${maxLimit} || offset < 0 || offset > ${maxOffset})
        return CURLE_BAD_FUNCTION_ARGUMENT;
    curl = curl_easy_init();
    if (!curl) return CURLE_FAILED_INIT;
    snprintf(url, sizeof(url), "%s?limit=%d&offset=%d", ${quoted}, limit, offset);
    snprintf(authorization, sizeof(authorization), "X-API-Key: %s", api_key);
    headers = curl_slist_append(NULL, authorization);
    next = headers ? curl_slist_append(headers, "Accept: application/json") : NULL;
    if (!next) { curl_slist_free_all(headers); curl_easy_cleanup(curl); return CURLE_OUT_OF_MEMORY; }
    headers = next;
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    curl_easy_setopt(curl, CURLOPT_FAILONERROR, 1L);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, publication_append);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &buffer);
    result = curl_easy_perform(curl);
    if (result == CURLE_OK) {
        result = curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        if (result == CURLE_OK && (status < 200 || status >= 300)) result = CURLE_HTTP_RETURNED_ERROR;
    }
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    if (result == CURLE_OK) *json = buffer.data;
    else free(buffer.data);
    return result;
}
`;
  return `// Generated read-only client. Supply an issued API key at runtime.
export class PublicationClient {
  constructor(private readonly apiKey: string, private readonly http: typeof fetch = fetch) {
    if (!apiKey || /[\\r\\n]/.test(apiKey)) throw new Error("An API key without newlines is required");
  }
  async readPage(limit = 50, offset = 0, signal?: AbortSignal): Promise<unknown> {
    if (!Number.isInteger(limit) || limit < 1 || limit > ${maxLimit} || !Number.isInteger(offset) || offset < 0 || offset > ${maxOffset})
      throw new RangeError("Invalid page bounds");
    const query = new URLSearchParams({limit: String(limit), offset: String(offset)});
    const response = await this.http(${quoted} + "?" + query, {
      headers: {"X-API-Key": this.apiKey, Accept: "application/json"}, signal, redirect: "error",
    });
    if (!response.ok) throw new Error("Publication HTTP " + response.status + ": " + await response.text());
    const value: unknown = await response.json();
    const check = (item: unknown): void => {
      if (typeof item === "number" && Number.isInteger(item) && !Number.isSafeInteger(item))
        throw new RangeError("Response contains an integer outside the safe JavaScript number range");
      if (Array.isArray(item)) item.forEach(check);
      else if (item && typeof item === "object") Object.values(item).forEach(check);
    };
    check(value);
    return value;
  }
}
`;
}
