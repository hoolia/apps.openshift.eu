# Tests of charts/chatwoot/files/customer_page.rb: the customer page at / and its login.
#
# ruby ci/test_chatwoot_customer_page.rb
require 'base64'
require 'json'
require 'minitest/autorun'
require 'openssl'
require 'tmpdir'

load File.expand_path('../charts/chatwoot/files/customer_page.rb', __dir__)

class CustomerPageTest < Minitest::Test
  PAGE = File.expand_path('../charts/chatwoot/files/customer_page.html', __dir__)
  ISSUER = 'https://login.example.com/realms/customers'.freeze
  KEY = 'k' * 48
  TOKEN = 'website-token-1'.freeze
  DISCOVERY = {
    'issuer' => ISSUER,
    'authorization_endpoint' => "#{ISSUER}/protocol/openid-connect/auth",
    'token_endpoint' => "#{ISSUER}/protocol/openid-connect/token",
    'userinfo_endpoint' => "#{ISSUER}/protocol/openid-connect/userinfo"
  }.freeze

  def setup
    @gets = []
    @userinfo = [200, JSON.generate('sub' => 'u-1', 'email' => 'Ann@Example.com', 'email_verified' => true, 'name' => 'Ann')]
    @discovery = [200, JSON.generate(DISCOVERY)]
    @widget = [TOKEN, KEY]
  end

  def config(**over)
    { 'accountId' => 1, 'inbox' => 'Website', 'title' => 'Support <Desk>', 'portalUrl' => 'https://portal.example.com/my',
      'portalText' => 'Orders', 'staffUrl' => '/app/login', 'issuerUrl' => ISSUER, 'clientId' => 'site' }.merge(over)
  end

  def app(**over)
    downstream = ->(env) { [299, {}, ["chatwoot #{env['PATH_INFO']}"]] }
    http_get = lambda do |url, headers|
      @gets << [url, headers]
      url.end_with?('/.well-known/openid-configuration') ? @discovery : @userinfo
    end
    ChartCustomerPage.new(downstream, page: PAGE, config: JSON.generate(config(**over)), frontend: 'https://chat.example.com/',
                                      http_get: http_get, inbox: ->(_account, _name) { @widget })
  end

  def jwt(claims)
    head = Base64.urlsafe_encode64('{"alg":"RS256"}', padding: false)
    "#{head}.#{Base64.urlsafe_encode64(JSON.generate(claims), padding: false)}.sig"
  end

  def post(app, token)
    env = { 'PATH_INFO' => '/customer/identity', 'REQUEST_METHOD' => 'POST' }
    env['HTTP_AUTHORIZATION'] = "Bearer #{token}" if token
    app.call(env)
  end

  def settings(body)
    JSON.parse(body[%r{<script type="application/json" id="cw-settings">(.*?)</script>}m, 1])
  end

  def test_page_at_root_with_the_inbox_and_the_login
    status, headers, body = app.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
    assert_equal 200, status
    html = body.join
    s = settings(html)
    assert_equal TOKEN, s['websiteToken']
    assert_equal 'https://chat.example.com', s['baseUrl']
    assert_equal '/app/login', s['staffUrl']
    assert_equal 'https://portal.example.com/my', s['portalUrl']
    assert_equal({ 'clientId' => 'site', 'authorize' => DISCOVERY['authorization_endpoint'],
                   'token' => DISCOVERY['token_endpoint'], 'identity' => '/customer/identity' }, s['login'])
    # the key never reaches the browser
    refute_includes html, KEY
    assert_includes html, '<title>Support &lt;Desk&gt;</title>'
    nonce = headers['content-security-policy'][/'nonce-([^']+)'/, 1]
    assert_includes html, %(<script nonce="#{nonce}">)
    refute_includes html, '__NONCE__'
    assert_includes headers['content-security-policy'], "frame-ancestors 'none'"
    assert_includes headers['content-security-policy'], "connect-src 'self' https://login.example.com;"
    assert_equal 'no-store', headers['cache-control']
  end

  def test_a_title_cannot_end_the_script
    _, _, body = app(title: '</script><script>alert(1)</script>').call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
    html = body.join
    refute_includes html, '</script><script>alert(1)'
    assert_equal '</script><script>alert(1)</script>', settings(html)['title']
  end

  def test_head_has_no_body
    status, _, body = app.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'HEAD')
    assert_equal [200, []], [status, body]
  end

  def test_without_the_inbox_root_stays_chatwoot
    @widget = nil
    assert_equal [299, {}, ['chatwoot /']], app.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
  end

  def test_other_paths_are_chatwoot
    a = app
    %w[/app/login /widget /index.html /customer].each do |path|
      assert_equal 299, a.call('PATH_INFO' => path, 'REQUEST_METHOD' => 'GET')[0], path
    end
    assert_equal 299, a.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'POST')[0]
    assert_equal 405, a.call('PATH_INFO' => '/customer/identity', 'REQUEST_METHOD' => 'GET')[0]
  end

  def test_no_login_without_provider_settings
    a = app('issuerUrl' => '', 'clientId' => '')
    _, _, body = a.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
    assert_nil settings(body.join)['login']
    assert_equal 503, post(a, jwt('iss' => ISSUER, 'azp' => 'site', 'sub' => 'u-1'))[0]
    assert_empty @gets
  end

  def test_no_login_when_discovery_names_another_issuer
    @discovery = [200, JSON.generate(DISCOVERY.merge('issuer' => 'https://evil.example.com'))]
    a = app
    _, _, body = a.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
    assert_nil settings(body.join)['login']
    assert_equal 503, post(a, jwt('iss' => ISSUER, 'azp' => 'site', 'sub' => 'u-1'))[0]
  end

  def test_identity_signs_the_verified_email
    status, headers, body = post(app, jwt('iss' => ISSUER, 'azp' => 'site', 'sub' => 'u-1'))
    assert_equal 200, status
    assert_equal 'no-store', headers['cache-control']
    got = JSON.parse(body.join)
    assert_equal 'ann@example.com', got['identifier']
    assert_equal 'Ann', got['name']
    assert_equal OpenSSL::HMAC.hexdigest('SHA256', KEY, 'ann@example.com'), got['identifier_hash']
    # the provider checked the token itself
    url, headers = @gets.last
    assert_equal DISCOVERY['userinfo_endpoint'], url
    assert_match(/\ABearer /, headers['Authorization'])
  end

  def test_tokens_of_another_client_or_provider_are_refused_unasked
    a = app
    a.call('PATH_INFO' => '/', 'REQUEST_METHOD' => 'GET')
    asked = @gets.length
    [jwt('iss' => ISSUER, 'azp' => 'other', 'sub' => 'u-1'),
     jwt('iss' => 'https://login.example.com/realms/staff', 'azp' => 'site', 'sub' => 'u-1'),
     'not-a-jwt', "#{jwt('iss' => ISSUER)}.extra"].each do |token|
      assert_equal 401, post(a, token)[0], token
    end
    assert_equal 401, post(a, nil)[0]
    assert_equal asked, @gets.length
  end

  def test_unverified_or_foreign_userinfo_is_refused
    token = jwt('iss' => ISSUER, 'azp' => 'site', 'sub' => 'u-1')
    [[200, { 'sub' => 'u-1', 'email' => 'ann@example.com', 'email_verified' => false }],
     [200, { 'sub' => 'u-2', 'email' => 'ann@example.com', 'email_verified' => true }],
     [200, { 'sub' => 'u-1', 'email' => 'not an email', 'email_verified' => true }],
     [401, { 'error' => 'invalid_token' }]].each do |status, user|
      @userinfo = [status, JSON.generate(user)]
      assert_equal 401, post(app, token)[0], user.inspect
    end
    @userinfo = [200, 'not json']
    assert_equal 401, post(app, token)[0]
  end

  def test_audience_counts_without_azp
    @userinfo = [200, JSON.generate('sub' => 'u-1', 'email' => 'ann@example.com', 'email_verified' => true)]
    assert_equal 200, post(app, jwt('iss' => ISSUER, 'aud' => %w[site api], 'sub' => 'u-1'))[0]
    assert_equal 401, post(app, jwt('iss' => ISSUER, 'aud' => 'api', 'sub' => 'u-1'))[0]
  end
end
