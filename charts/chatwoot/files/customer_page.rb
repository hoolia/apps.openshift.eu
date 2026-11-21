# The customer page of the chart (sso.public.customerPage): GET / of the host is a full-window
# chat of one Website inbox, and POST /customer/identity signs a logged-in customer for it.
#
# Loaded by chart_initializer.rb in Chatwoot web, which puts ChartCustomerPage in front of the
# routes. Configuration from the environment:
#   CHART_CUSTOMER_PAGE         the page template (customer_page.html)
#   CHART_CUSTOMER_PAGE_CONFIG  JSON: accountId, inbox, title, portalUrl, portalText,
#                               staffUrl, and for the login issuerUrl and clientId
#   FRONTEND_URL                the public https address of the instance
#
# The page needs no session: oauth2-proxy lets GET / and POST /customer/identity through
# without a login and strips any user header. The identity endpoint answers only for an
# access token of the configured OIDC client: the provider's userinfo endpoint checks
# signature, expiry and session, and its answer must carry a verified email. The answer is
# Chatwoot identity validation for that email, HMAC-SHA256 of the inbox's own key; the key
# never leaves the server, the token is never stored.
require 'json'
require 'net/http'
require 'openssl'
require 'securerandom'
require 'uri'

class ChartCustomerPage
  IDENTITY_PATH = '/customer/identity'.freeze
  EMAIL = /\A[^@\s<>]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}\z/
  CACHE_SECONDS = 60
  DISCOVERY_SECONDS = 600
  TIMEOUT = 5

  # http_get: (url, headers) -> [status, body]; the default is Net::HTTP without redirects.
  # inbox: (account id, name) -> [website token, hmac key] or nil; the default reads Chatwoot.
  def initialize(app, page: ENV.fetch('CHART_CUSTOMER_PAGE'), config: ENV.fetch('CHART_CUSTOMER_PAGE_CONFIG'),
                 frontend: ENV.fetch('FRONTEND_URL', ''), http_get: nil, inbox: nil, clock: nil)
    @app = app
    @template = File.read(page)
    @config = JSON.parse(config)
    @frontend = frontend.to_s.chomp('/')
    @http_get = http_get || method(:net_get)
    @inbox = inbox || method(:chatwoot_inbox)
    @clock = clock || -> { Process.clock_gettime(Process::CLOCK_MONOTONIC) }
    @lock = Mutex.new
    @cached = {}
  end

  def call(env)
    path = env['PATH_INFO']
    method = env['REQUEST_METHOD']
    return page(env, method) if path == '/' && %w[GET HEAD].include?(method)
    return identity(env) if path == IDENTITY_PATH && method == 'POST'
    return [405, { 'content-type' => 'text/plain', 'allow' => 'POST' }, ['Method not allowed']] if path == IDENTITY_PATH

    @app.call(env)
  end

  def login?
    !@config['issuerUrl'].to_s.empty? && !@config['clientId'].to_s.empty?
  end

  private

  # --- the page ---------------------------------------------------------------------
  def page(env, method)
    widget = cached(:inbox, CACHE_SECONDS) { @inbox.call(@config['accountId'], @config['inbox']) }
    # no Website inbox (yet): / stays what Chatwoot serves, the staff login
    return @app.call(env) if widget.nil?

    nonce = SecureRandom.base64(18)
    oidc = login? ? discovery : nil
    settings = {
      baseUrl: @frontend, websiteToken: widget[0], title: @config['title'].to_s,
      portalUrl: @config['portalUrl'].to_s, portalText: @config['portalText'].to_s,
      staffUrl: @config['staffUrl'].to_s.empty? ? '/app/login' : @config['staffUrl'].to_s,
      login: oidc && widget[1] ? { clientId: @config['clientId'], authorize: oidc['authorization_endpoint'],
                                   token: oidc['token_endpoint'], identity: IDENTITY_PATH } : nil
    }
    # block forms: a replacement string would read the backslashes in the values
    body = @template.gsub('__NONCE__') { nonce }
                    .sub('__SETTINGS__') { json_for_script(settings) }
                    .sub('__TITLE__') { html(settings[:title]) }
    connect = ["'self'"]
    connect << origin(oidc['token_endpoint']) if settings[:login]
    headers = {
      'content-type' => 'text/html; charset=utf-8',
      'cache-control' => 'no-store',
      'referrer-policy' => 'no-referrer',
      'x-content-type-options' => 'nosniff',
      'content-security-policy' => "default-src 'self'; script-src 'self' 'nonce-#{nonce}'; " \
                                   "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; " \
                                   "connect-src #{connect.uniq.join(' ')}; frame-src 'self'; " \
                                   "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    }
    [200, headers, method == 'HEAD' ? [] : [body]]
  end

  # the provider's endpoints, from its discovery document; nil while it is not readable
  def discovery
    cached(:discovery, DISCOVERY_SECONDS) do
      status, body = @http_get.call("#{@config['issuerUrl'].chomp('/')}/.well-known/openid-configuration", {})
      doc = status == 200 ? JSON.parse(body) : nil
      keys = %w[authorization_endpoint token_endpoint userinfo_endpoint]
      doc if doc.is_a?(Hash) && doc['issuer'] == @config['issuerUrl'] &&
             keys.all? { |k| doc[k].to_s.start_with?('https://') }
    rescue StandardError => e
      warn "customer page: provider discovery failed: #{e.class}"
      nil
    end
  end

  # --- the identity -----------------------------------------------------------------
  def identity(env)
    widget = login? ? cached(:inbox, CACHE_SECONDS) { @inbox.call(@config['accountId'], @config['inbox']) } : nil
    oidc = widget && widget[1] ? discovery : nil
    return json(503, error: 'customer login is off') if oidc.nil?

    auth = env['HTTP_AUTHORIZATION'].to_s
    return denied unless auth.start_with?('Bearer ')

    token = auth[7..].strip
    claims = claims_of(token)
    # a token of another client or provider, even a valid one, is no customer login
    return denied unless claims && claims['iss'] == @config['issuerUrl'] && client_of?(claims)

    status, body = @http_get.call(oidc['userinfo_endpoint'], { 'Authorization' => "Bearer #{token}" })
    return denied unless status == 200

    user = JSON.parse(body) rescue nil
    return denied unless user.is_a?(Hash)

    email = user['email'].to_s.strip.downcase
    return denied unless user['email_verified'] == true && EMAIL.match?(email) && user['sub'] == claims['sub']

    name = user['name'].to_s.strip
    json(200, identifier: email, email: email, name: name.empty? ? email : name,
              identifier_hash: OpenSSL::HMAC.hexdigest('SHA256', widget[1], email))
  rescue StandardError => e
    warn "customer page: identity failed: #{e.class}"
    denied
  end

  def client_of?(claims)
    client = @config['clientId']
    return claims['azp'] == client if claims.key?('azp')

    Array(claims['aud']).include?(client)
  end

  # the claims of a JWT, unverified: used only to pick the provider and client, which then check it
  def claims_of(token)
    parts = token.split('.')
    return nil unless parts.length == 3

    payload = parts[1].tr('-_', '+/')
    data = JSON.parse(payload.unpack1('m'))
    data.is_a?(Hash) ? data : nil
  rescue ArgumentError, JSON::ParserError
    nil
  end

  def denied
    json(401, error: 'not logged in')
  end

  def json(status, body)
    [status, { 'content-type' => 'application/json', 'cache-control' => 'no-store' }, [JSON.generate(body)]]
  end

  # --- helpers ----------------------------------------------------------------------
  def cached(key, seconds)
    now = @clock.call
    @lock.synchronize do
      hit = @cached[key]
      return hit[0] if hit && hit[1] > now
    end
    value = yield
    # a miss is kept briefly too, so a missing inbox or provider is not asked on every request
    @lock.synchronize { @cached[key] = [value, now + (value.nil? ? 10 : seconds)] }
    value
  end

  def json_for_script(value)
    # a string in a script element must not end it: <, >, & and the line separators as \u escapes
    JSON.generate(value).gsub(/[<>&\u2028\u2029]/) { |c| format('\\u%04x', c.ord) }
  end

  def html(text)
    text.to_s.gsub('&', '&amp;').gsub('<', '&lt;').gsub('>', '&gt;').gsub('"', '&quot;')
  end

  def origin(url)
    u = URI.parse(url)
    "#{u.scheme}://#{u.host}#{[80, 443].include?(u.port) ? '' : ":#{u.port}"}"
  end

  def net_get(url, headers)
    uri = URI.parse(url)
    raise ArgumentError, 'https only' unless uri.scheme == 'https'

    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.open_timeout = TIMEOUT
    http.read_timeout = TIMEOUT
    request = Net::HTTP::Get.new(uri.request_uri, { 'Accept' => 'application/json' }.merge(headers))
    response = http.request(request)
    [response.code.to_i, response.body.to_s]
  end

  def chatwoot_inbox(account_id, name)
    ActiveRecord::Base.connection_pool.with_connection do
      inbox = Inbox.find_by(account_id: account_id, name: name, channel_type: 'Channel::WebWidget')
      next nil unless inbox

      key = inbox.channel.hmac_token.to_s
      [inbox.channel.website_token, key.empty? ? nil : key]
    end
  end
end
