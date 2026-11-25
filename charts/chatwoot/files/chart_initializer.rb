# Settings of the chart that Chatwoot has no environment variable for; mounted as
# config/initializers/zz_chart.rb, so it runs after Chatwoot's own initializers.

if ENV['ACTIVE_STORAGE_SERVICE'] == 's3_compatible'
  # the bucket endpoint is reachable only inside the cluster: Chatwoot streams the files
  # under its own host instead of redirecting the browser to the bucket
  Rails.application.config.active_storage.resolve_model_to_route = :rails_storage_proxy
  Rails.application.config.after_initialize do
    ActiveStorage.resolve_model_to_route = :rails_storage_proxy
  end
end

if ENV['SMTP_ADDRESS'].to_s.empty?
  # without a mail server nothing is sent; a failed delivery would retry in the worker
  ActiveSupport.on_load(:action_mailer) { self.perform_deliveries = false }
end

if ENV.key?('CHATWOOT_SESSION_HOURS')
  # a dashboard session ends after this many hours (Chatwoot sets two months); the next
  # one needs a login at the OIDC provider, which checks the group again. The dashboard
  # keeps its session cookie for whole days rounded down, so below 25 hours the cookie
  # expires at once and every login loops: refuse to start instead
  hours = ENV['CHATWOOT_SESSION_HOURS'].to_s
  unless hours.match?(/\A[1-9][0-9]*\z/) && (25..720).cover?(Integer(hours, 10))
    raise ArgumentError, "CHATWOOT_SESSION_HOURS must be a whole number from 25 to 720, got #{hours.inspect}"
  end
  DeviseTokenAuth.token_lifespan = Integer(hours, 10).hours
end

bridges = ENV.values_at('CHART_AGENT_BOT_URL', 'CHART_ODOO_BRIDGE_URL').map(&:to_s).reject(&:empty?)
unless bridges.empty?
  # Chatwoot refuses webhooks to private addresses (SafeFetch). The agent bot's bridge and
  # the Odoo bridge are Services of this release: allow a private address for a webhook to
  # their addresses only
  Rails.application.config.to_prepare do
    SafeFetch.singleton_class.prepend(Module.new do
      def allow_private_network?
        Thread.current[:chart_agent_bot_webhook] || super
      end
    end)
    Webhooks::Trigger.prepend(Module.new do
      private

      define_method(:perform_request) do
        return super() unless bridges.any? { |bridge| @url.to_s.start_with?(bridge) }

        Thread.current[:chart_agent_bot_webhook] = true
        begin
          super()
        ensure
          Thread.current[:chart_agent_bot_webhook] = nil
        end
      end
    end)
  end
end

unless ENV['CHART_CUSTOMER_PAGE'].to_s.empty?
  # GET / of the host is the customer page, POST /customer/identity its login
  # (customer_page.rb, next to the page template); every other path is Chatwoot's own
  load File.join(File.dirname(ENV['CHART_CUSTOMER_PAGE']), 'customer_page.rb')
  Rails.application.config.middleware.use ChartCustomerPage
end

# The Platform API never hands out a super admin: no login link, no API token, no change
# and no adoption of an existing super admin by a Platform App. The login bridge uses this
# API; a super admin logs in with its password at /super_admin only.
Rails.application.config.to_prepare do
  Platform::Api::V1::UsersController.class_eval do
    before_action :chart_refuse_super_admin, only: %i[login token update destroy]
    before_action :chart_refuse_super_admin_email, only: %i[create]

    private

    def chart_refuse_super_admin
      render json: { error: 'super admin' }, status: :forbidden if @resource.is_a?(SuperAdmin)
    end

    def chart_refuse_super_admin_email
      user = User.from_email(params[:email].to_s)
      render json: { error: 'super admin' }, status: :forbidden if user.is_a?(SuperAdmin)
    end
  end
end

# A visitor's browser never joins, reads or rewrites another person's contact. Loaded by
# zz_chart.rb. Written against Chatwoot v4.19: recheck it at every image upgrade.
#
# - A widget or public inbox request merges nothing, except a valid identifier hash into
#   the contact of exactly that identifier.
# - Without such a hash only an unidentified contact that belongs to this one browser
#   session changes; a hashed request sets no other address than the signed one.
# - hmac_verified holds only while the session's source id names the identifier of its
#   contact. That binding never moves.
# - What goes to a browser holds no contact's name, address, phone or attributes.
# Staff and the bridges use the account API, which this leaves alone.
require 'digest'

module ChartContactGuard
  module_function

  EMAIL = /\A[^@\s<>]+@[^@\s<>]+\z/

  def binding_of(identifier)
    "id-#{Digest::SHA256.hexdigest(identifier.to_s)[0, 32]}-"
  end

  def bound?(contact_inbox, identifier)
    identifier.present? && contact_inbox.source_id.to_s.start_with?(binding_of(identifier))
  end

  # an unidentified contact of this one session: what a visitor without a hash may change
  def own?(contact)
    contact.identifier.blank? && ContactInbox.where(contact_id: contact.id).count <= 1
  end

  def for_browser(value)
    case value
    when Array then value.map { |v| for_browser(v) }
    when Hash
      return value.slice('id', 'type') if value['type'] == 'contact' && value.key?('identifier')

      value.to_h do |key, v|
        kept = case key
               when 'contact' then v.is_a?(Hash) ? v.slice('id', 'pubsub_token') : v
               when 'contact_inbox' then v.is_a?(Hash) ? v.slice('id', 'inbox_id') : v
               else for_browser(v)
               end
        [key, kept]
      end
    else value
    end
  end

  def rewrite(response)
    return unless response.media_type == 'application/json' && response.body.present?

    data = for_browser(JSON.parse(response.body))
    data = yield(data) if block_given? && data.is_a?(Hash)
    response.body = data.to_json
  end

  module Identify
    attr_accessor :chart_proven

    def perform
      @chart_subject = @contact
      wanted = @params.to_h.symbolize_keys.except(:identifier)
      if chart_proven.present?
        wanted[:identifier] = chart_proven
        # the hash proves one address: the identifier itself
        wanted.delete(:email)
        wanted[:email] = chart_proven.downcase if chart_proven.match?(EMAIL)
      elsif !ChartContactGuard.own?(@contact)
        return @contact
      end
      @params = wanted
      @discard_invalid_attrs = true
      super
    end

    private

    def merge_contacts?(existing, key)
      return false if existing.blank? || existing.id == @contact.id
      return true if key == :identifier && chart_proven.present? && existing.identifier == chart_proven

      @attributes_to_update.delete(key)
      false
    end

    # the signed identifier's contact keeps what it has; the session's typed address stays out
    def process_contact_merge(existing)
      @contact.update!(email: nil, phone_number: nil)
      super
    end
  end

  module Inbox
    def hmac_verified
      self[:hmac_verified] == true && ChartContactGuard.bound?(self, contact&.identifier)
    end

    def hmac_verified?
      hmac_verified
    end
  end

  module WidgetBase
    private

    def conversations
      return super unless @contact_inbox.hmac_verified?

      ids = @contact.contact_inboxes.where(inbox_id: auth_token_params[:inbox_id], hmac_verified: true)
                    .select(&:hmac_verified?).map(&:id)
      @conversations = @contact.conversations.where(contact_inbox_id: ids)
    end
  end

  module WidgetContacts
    def update
      id = params[:identifier].to_s
      proven = id.present? && @contact.identifier == id
      chart_identify(@contact, (id if proven))
    end

    def set_user
      id = params[:identifier].to_s
      return chart_identify(@contact, nil) if id.blank?

      ActiveRecord::Base.transaction do
        @contact_inbox.lock!
        @contact = @contact_inbox.contact.reload
        mine = @contact.identifier.present? ? @contact.identifier == id : ChartContactGuard.own?(@contact)
        unless mine
          @contact_inbox, @widget_auth_token = build_contact_inbox_with_token(@web_widget)
          @contact = @contact_inbox.contact
        end
        chart_identify(@contact, id)
        @contact_inbox.reload
        chart_bind(id)
      end
    end

    private

    # an identifier needs its hash on every widget, hmac_mandatory or not
    def validate_hmac
      return super if params[:identifier].blank?

      render json: { error: 'HMAC failed: Invalid Identifier Hash Provided' }, status: :unauthorized unless valid_hmac?
    end

    def chart_identify(contact, proven)
      action = ContactIdentifyAction.new(contact: contact, params: permitted_params.to_h.deep_symbolize_keys,
                                         discard_invalid_attrs: true)
      action.chart_proven = proven
      @contact = action.perform
    end

    def chart_bind(id)
      raise ActiveRecord::RecordInvalid, @contact_inbox unless @contact_inbox.contact.identifier == id

      changes = { hmac_verified: true }
      changes[:source_id] = "#{ChartContactGuard.binding_of(id)}#{SecureRandom.uuid}" unless ChartContactGuard.bound?(@contact_inbox, id)
      @contact_inbox.update!(changes)
      return unless changes.key?(:source_id)

      payload = { source_id: @contact_inbox.source_id, inbox_id: @web_widget.inbox.id }
      @widget_auth_token = ::Widget::TokenService.new(payload: payload).generate_token
    end
  end

  module PublicContacts
    def create
      id = params[:identifier].to_s
      proven = id.present? && valid_hmac?
      # the builder gets no address and no phone
      attrs = { name: params[:name].presence }
      attrs[:identifier] = id if proven
      source = params[:source_id].to_s
      # only a verified login makes a source id that names an identifier
      source = SecureRandom.uuid if source.empty? || source.start_with?('id-')
      @contact_inbox = ::ContactInboxWithContactBuilder.new(
        source_id: source,
        inbox: @inbox_channel.inbox, contact_attributes: attrs
      ).perform
      chart_identify((id if proven && @contact_inbox.contact.identifier == id))
      @contact_inbox.reload
    end

    def update
      id = params[:identifier].to_s
      chart_identify((id if id.present? && valid_hmac? && @contact_inbox.contact.identifier == id))
      @contact_inbox.reload
    end

    private

    def chart_identify(proven)
      action = ContactIdentifyAction.new(contact: @contact_inbox.contact,
                                         params: permitted_params.to_h.deep_symbolize_keys.except(:identifier_hash),
                                         discard_invalid_attrs: true)
      action.chart_proven = proven
      action.perform
    end
  end

  module Cable
    private

    def contact_inbox_tokens(contact_inbox)
      return [contact_inbox.pubsub_token] unless contact_inbox.hmac_verified?

      contact_inbox.contact.contact_inboxes.where(hmac_verified: true).select(&:hmac_verified?).filter_map(&:pubsub_token)
    end
  end

  module CableJob
    private

    def broadcast_to_members(members, event_name, data)
      browsers = ContactInbox.where(pubsub_token: members).pluck(:pubsub_token)
      others = members - browsers
      staff = User.where(pubsub_token: others).pluck(:pubsub_token) + others.grep(/\Aaccount_[0-9]+\z/)
      super(staff, event_name, data) if staff.any?
      super(browsers, event_name, ChartContactGuard.for_browser(data.as_json)) if browsers.any?
    end
  end
end

Rails.application.config.to_prepare do
  ContactIdentifyAction.prepend ChartContactGuard::Identify
  ContactInbox.prepend ChartContactGuard::Inbox
  ActionCableListener.prepend ChartContactGuard::Cable
  ActionCableBroadcastJob.prepend ChartContactGuard::CableJob

  Api::V1::Widget::BaseController.class_eval do
    prepend ChartContactGuard::WidgetBase
    after_action :chart_for_browser

    private

    def chart_for_browser
      ChartContactGuard.rewrite(response)
    end
  end

  Api::V1::Widget::ContactsController.class_eval do
    prepend ChartContactGuard::WidgetContacts
    before_action :chart_own_contact_only, only: [:destroy_custom_attributes]

    private

    def chart_own_contact_only
      head :ok unless ChartContactGuard.own?(@contact)
    end

    def chart_for_browser
      ChartContactGuard.rewrite(response) do |data|
        next data.slice('error') if data.key?('error')

        out = data.slice('id', 'has_email', 'has_name', 'has_phone_number', 'widget_auth_token')
        inbox = @contact_inbox&.reload
        if inbox&.hmac_verified?
          id = inbox.contact.identifier
          out['identifier'] = id if data.key?('identifier')
          # the signed address is known: the pre-chat form does not ask for it again
          out['has_email'] = true if id.match?(ChartContactGuard::EMAIL)
        end
        out
      end
    end
  end

  Public::Api::V1::InboxesController.class_eval do
    after_action :chart_for_browser

    private

    def chart_for_browser
      ChartContactGuard.rewrite(response)
    end
  end

  Public::Api::V1::Inboxes::ContactsController.class_eval do
    prepend ChartContactGuard::PublicContacts

    private

    def chart_for_browser
      ChartContactGuard.rewrite(response) { |data| data.slice('id', 'source_id', 'pubsub_token', 'error') }
    end
  end
end

# What a request's query and the widget's page may carry.
#
# - The widget's and the public API's paths take the listed query keys only, in their format.
# - The cw_conversation value is one token; any other value is dropped whole.
# - A frame of the widget loads this host's help center or the video meeting only, and the
#   page runs its own scripts only.
# - An embedded widget takes messages from the page that embeds it only.
# - sdk.js is revalidated at each load.
require 'securerandom'

module ChartQueryGuard
  API = %r{\A/(api/v1/widget|public/api/v1)(/|\z)}
  PAGE = %r{\A/widget(\.[^/]*)?/?\z}
  SDK = '/packs/js/sdk.js'.freeze
  VALUES = {
    'website_token' => /\A[A-Za-z0-9_-]{1,128}\z/,
    'cw_conversation' => /\A(?=.{1,4096}\z)[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\z/,
    'locale' => /\A[A-Za-z0-9_-]{1,20}\z/,
    'chart_refused' => /\A1\z/,
    'chart_failed' => /\A1\z/,
    'chart_ended' => /\A1\z/,
    'chart_resumed' => /\A1\z/,
    'chart_unbound' => /\A1\z/,
    'chart_open' => /\A1\z/,
    'chart_login' => /\A[0-9]{10}\.[0-9a-f]{64}\z/,
    'before' => /\A[0-9]{1,20}\z/,
    'after' => /\A[0-9]{1,20}\z/
  }.freeze
  PAGE_KEYS = %w[website_token cw_conversation locale chart_refused chart_failed chart_ended chart_open chart_login chart_resumed chart_unbound].freeze

  module_function

  def decode(text)
    URI.decode_www_form_component(text.to_s.b, Encoding::BINARY)
  rescue ArgumentError
    nil
  end

  # a rule holds for the raw and for the decoded path
  def forms(path)
    raw = path.to_s.b
    [raw, decode(raw.gsub('+', '%2B')) || raw].map { |form| form.squeeze('/') }.uniq
  end

  # the allowed pairs, and whether anything was dropped. Of site and token the first counts,
  # of the others the last
  def clean(query, keys)
    kept = {}
    dropped = after_token = refused = false
    query.to_s.b.split('&').each do |pair|
      next if pair.empty?

      key, value = pair.split('=', 2).map { |part| decode(part) }
      first = %w[website_token cw_conversation].include?(key)
      if keys.include?(key) && value.to_s.match?(VALUES[key]) && !(first && kept.key?(key))
        kept[key] = value
      else
        dropped = true
        refused ||= after_token || key == 'cw_conversation'
      end
      after_token ||= key == 'cw_conversation'
    end
    if refused
      kept.delete('cw_conversation')
      # the page reports a refused session to the SDK
      kept['chart_refused'] = '1' if keys.include?('chart_refused')
    end
    [URI.encode_www_form(kept), dropped]
  end

  # the file ActionDispatch::Static serves for a path
  def file(path)
    plain = Rack::Utils.unescape_path(path.to_s.b.chomp('/'))
    Rack::Utils.clean_path_info(plain) if Rack::Utils.valid_path?(plain)
  rescue ArgumentError
    nil
  end

  def api_keys(method, paths)
    keys = paths.any? { |path| path.start_with?('/public/') } ? %w[locale] : %w[website_token locale]
    list = method == 'GET' && paths.any? { |path| path.match?(%r{/messages(\.[^/]*)?/?\z}) }
    list ? keys + %w[before after] : keys
  end

  class Middleware
    def initialize(app)
      @app = app
    end

    def call(env)
      paths = ChartQueryGuard.forms(env['PATH_INFO'])
      method = env['REQUEST_METHOD'] == 'HEAD' ? 'GET' : env['REQUEST_METHOD']
      if paths.any? { |path| path.match?(PAGE) }
        return [405, { 'allow' => 'GET, HEAD', 'cache-control' => 'no-store', 'content-length' => '0' }, []] unless method == 'GET'

        query, dropped = ChartQueryGuard.clean(env['QUERY_STRING'], PAGE_KEYS)
        # loaded again with the allowed query only
        return [302, { 'location' => "/widget?#{query}", 'cache-control' => 'no-store', 'content-length' => '0' }, []] if dropped

        env['QUERY_STRING'] = query
      elsif paths.any? { |path| path.match?(API) }
        env['QUERY_STRING'], = ChartQueryGuard.clean(env['QUERY_STRING'], ChartQueryGuard.api_keys(method, paths))
      end

      status, headers, body = @app.call(env)
      if ChartQueryGuard.file(env['PATH_INFO']) == SDK && [200, 304].include?(status)
        headers = headers.to_h.reject { |key, _| key.to_s.casecmp?('cache-control') }.merge('cache-control' => 'no-cache')
      end
      [status, headers, body]
    end
  end
end

module ChartWidgetPage
  # scripts: the page's own only. The other sources are those of what the widget loads:
  # its bundle, a CDN over https, workers from blob:
  POLICY = "script-src 'self' https: blob: 'unsafe-eval' 'nonce-%s'".freeze

  # first in the page, ahead of the bundle
  SCRIPT = <<~'JS'.freeze
    (function () {
      var PREFIX = 'chatwoot-widget:';
      var MEETING = 'https://examples.realtime.cloudflare.com';
      var embedded = window.parent !== window;

      function parse(value) {
        try { return new URL(String(value), location.href); } catch (e) { return null; }
      }
      // this host's help center: what the widget's own article list links to
      function article(value) {
        var to = parse(value);
        return !!to && (to.protocol === 'https:' || to.protocol === 'http:') && to.origin === location.origin &&
          !to.username && !to.password && to.pathname.indexOf('/hc/') === 0 && !/%(2f|5c)/i.test(to.pathname);
      }
      function frame(value) {
        if (value === null || value === undefined || String(value) === '') { return value; }
        if (String(value) === 'about:blank' || article(value)) { return value; }
        var to = parse(value);
        var meeting = to && to.origin === MEETING && to.pathname.indexOf('/meeting/') === 0;
        return meeting ? value : 'about:blank';
      }

      // every way to give a frame its address
      var proto = HTMLIFrameElement.prototype;
      var src = Object.getOwnPropertyDescriptor(proto, 'src');
      var attribute = Element.prototype.setAttribute, attributeNS = Element.prototype.setAttributeNS;
      function safe(name, value) {
        var key = String(name).toLowerCase();
        return key === 'src' ? frame(value) : key === 'srcdoc' ? '' : value;
      }
      Object.defineProperty(proto, 'src', {
        configurable: true, enumerable: src.enumerable, get: src.get,
        set: function (value) { src.set.call(this, frame(value)); }
      });
      Object.defineProperty(proto, 'srcdoc', {
        configurable: true, enumerable: true, get: function () { return ''; }, set: function () {}
      });
      proto.setAttribute = function (name, value) { return attribute.call(this, name, safe(name, value)); };
      proto.setAttributeNS = function (space, name, value) { return attributeNS.call(this, space, name, safe(name, value)); };

      // the address itself, before the router reads it
      function refused(hash) {
        var at = hash.indexOf('?');
        if (at < 0) { return false; }
        return new URLSearchParams(hash.slice(at + 1)).getAll('link').some(function (link) { return !article(link); });
      }
      var replace = history.replaceState;
      function clean() {
        if (refused(location.hash)) { replace.call(history, history.state, '', '#/'); }
      }
      ['pushState', 'replaceState'].forEach(function (name) {
        var original = history[name];
        history[name] = function (state, title, url) {
          var to = url === null || url === undefined ? null : parse(url);
          if (to && refused(to.hash)) { url = to.href.split('#')[0] + '#/'; }
          return original.call(this, state, title, url);
        };
      });
      window.addEventListener('popstate', clean, true);
      window.addEventListener('hashchange', clean, true);

      window.addEventListener('message', function (e) {
        if (!embedded || e.source === window.parent) { return; }
        if (typeof e.data === 'string' && e.data.indexOf(PREFIX) === 0) { e.stopImmediatePropagation(); }
      }, true);
      clean();

      if (embedded && new URLSearchParams(location.search).get('chart_refused')) {
        window.parent.postMessage(PREFIX + JSON.stringify({
          event: 'error', errorType: 'SET_USER_ERROR', data: { error: 'the chat session was refused' }
        }), '*');
      }
    })();
  JS

  # around all of the controller's callbacks, so on every path and answer of the widget's page
  module Response
    private

    def process_action(*)
      super
      nonce = SecureRandom.base64(18)
      others = response.headers['Content-Security-Policy'].to_s.split(';').map(&:strip).reject(&:empty?)
      response.headers['Content-Security-Policy'] = (others + [format(ChartWidgetPage::POLICY, nonce)]).join('; ')
      return unless response.media_type == 'text/html'

      page = response.body.to_s.gsub(/<script(?=[\s>])/, %(<script nonce="#{nonce}"))
      response.body = page.sub('<head>') { %(<head><script nonce="#{nonce}">#{ChartWidgetPage::SCRIPT}</script>) }
    end
  end
end

Rails.application.config.middleware.insert_before 0, ChartQueryGuard::Middleware

Rails.application.config.to_prepare do
  WidgetsController.prepend ChartWidgetPage::Response
end

# sdk.js with the chart's guard ahead of the image's bytes.
#
# - The SDK handles messages of its own widget frame only.
# - A popout is opened by the widget's button or by the site's $chatwoot.popoutChatWindow().
# - The guard compares no host: a site that loads the chat through an alias keeps working.
# - A failure to build it is logged.
require 'digest'
require 'zlib'

module ChartSdkGuard
  SCRIPT = <<~'JS'.freeze
    /* chart: the widget's SDK takes messages from its own frame only */
    (function () {
      var PREFIX = 'chatwoot-widget:';
      function again() {
        var sdk = window.$chatwoot;
        // reset() is a logout: it keeps identifier and user, and nobody may log them in again
        if (!sdk || sdk.resetTriggered || !sdk.identifier || !sdk.user || !sdk.setUser) { return; }
        if (document.cookie.indexOf('cw_user_' + sdk.websiteToken + '=') < 0) { sdk.setUser(sdk.identifier, sdk.user); }
      }
      function allowed(e) {
        var frame = document.getElementById('chatwoot_live_chat_widget');
        if (!frame || !e.source || e.source !== frame.contentWindow) { return false; }
        try {
          var event = String(JSON.parse(e.data.slice(PREFIX.length)).event);
          if (event === 'loaded') { setTimeout(again, 0); }
          return event !== 'popoutChatWindow';
        } catch (err) {
          return false;
        }
      }
      window.addEventListener('message', function (e) {
        if (typeof e.data !== 'string' || e.data.indexOf(PREFIX) !== 0 || allowed(e)) { return; }
        e.stopImmediatePropagation();
      }, true);
    })();
  JS

  module_function

  def gzip?(accept)
    wanted = accept.to_s.downcase.split(',').to_h do |part|
      name, *params = part.split(';').map(&:strip)
      q = params.grep(/\Aq=/).first
      [name.to_s, q ? q[2..].to_f : 1.0]
    end
    wanted.fetch('gzip') { wanted.fetch('*', 0.0) }.positive?
  end

  # the two encodings of what is served, made once for each content of the file
  def build(original)
    @built ||= {}
    @built[Digest::SHA256.hexdigest(original)] ||= begin
      plain = "#{SCRIPT};\n".b + original
      tag = Digest::SHA256.hexdigest(plain)[0, 32]
      { nil => [plain.freeze, %("#{tag}")], 'gzip' => [Zlib.gzip(plain, level: 9).freeze, %("#{tag}-gzip")] }
    end
  end

  class Middleware
    # they describe the image's file, not what is served
    FILE_HEADERS = %w[content-length content-encoding last-modified etag accept-ranges content-md5].freeze
    # the app answers with the whole plain file
    # the bundler's builds of the SDK are not served
    COPY = %r{\A/vite/assets/sdk-[^/]*\z}
    CONDITIONS = %w[HTTP_IF_MODIFIED_SINCE HTTP_IF_NONE_MATCH HTTP_RANGE HTTP_IF_RANGE].freeze

    def initialize(app)
      @app = app
    end

    def call(env)
      method = env['REQUEST_METHOD']
      file = ChartQueryGuard.file(env['PATH_INFO']).to_s
      return [404, { 'cache-control' => 'no-store', 'content-length' => '0' }, []] if file.match?(COPY)
      return @app.call(env) unless %w[GET HEAD].include?(method) && file == ChartQueryGuard::SDK

      status, headers, body = @app.call(env.except(*CONDITIONS).merge('REQUEST_METHOD' => 'GET', 'HTTP_ACCEPT_ENCODING' => 'identity'))
      return [status, headers, body] unless status == 200

      headers = headers.to_h.transform_keys { |key| key.to_s.downcase }
      return [status, headers, body] if headers['content-encoding'].to_s !~ /\A(identity)?\z/

      original = ''.b
      body.each { |part| original << part.to_s.b }
      body.close if body.respond_to?(:close)
      guarded(env, headers, original)
    end

    private

    def guarded(env, headers, original)
      encoding = ('gzip' if ChartSdkGuard.gzip?(env['HTTP_ACCEPT_ENCODING']))
      bytes, tag = ChartSdkGuard.build(original).fetch(encoding)
      out = headers.except(*FILE_HEADERS).merge('etag' => tag, 'cache-control' => 'no-cache')
      out['vary'] = [out['vary'], 'Accept-Encoding'].compact.join(', ') unless out['vary'].to_s.downcase.include?('accept-encoding')
      known = env['HTTP_IF_NONE_MATCH'].to_s.split(',').map { |one| one.strip.delete_prefix('W/') }
      return [304, out.except('content-type'), []] if known.include?(tag) || known.include?('*')

      out['content-encoding'] = encoding if encoding
      out['content-length'] = bytes.bytesize.to_s
      [200, out, env['REQUEST_METHOD'] == 'HEAD' ? [] : [bytes]]
    rescue StandardError => e
      Rails.logger.error("chart: sdk.js served without its guard: #{e.class}")
      plain = headers.except('content-length').merge('content-length' => original.bytesize.to_s)
      [200, plain, env['REQUEST_METHOD'] == 'HEAD' ? [] : [original]]
    end
  end
end

Rails.application.config.middleware.insert_before 0, ChartSdkGuard::Middleware

# A login starts a chat session. Website widgets only.
#
# - A login starts a session (source id id-<identifier hash>-s-<uuid>, own auth and realtime
#   token) and ends the one it came from.
# - A verified session without that mark is refused on every widget path but the login,
#   hears nothing and verifies nothing. So is a login session after LIFETIME without a login.
# - The widget's page carries a login session only with the ticket of a login in that page
#   load; any other load of the page gets a new anonymous session. A login resumes the latest
#   login session of its address.
# - The widget asks the site for the login at each load and takes no input until that is bound.
# - A login that comes before the widget listens is given to it once.
module ChartSessionGuard
  module_function

  STARTED = /\Aid-[0-9a-f]{32}-s-/
  RESUME = 'X-Chart-Resume'.freeze
  ENDED = 'X-Chart-Session-Ended'.freeze
  PAGE = 'X-Chart-Page'.freeze
  LOGIN = 'X-Chart-Login'.freeze
  # the lifetime of the website's login (the www plugin's identity)
  LIFETIME = 12.hours
  TICKET_SECONDS = 120
  # clock skew allowed between the web pods
  SKEW_SECONDS = 5
  TICKET = /\A[0-9]{10}\.[0-9a-f]{64}\z/
  NOTICE = 'This chat session ended. Reload the page to continue.'.freeze

  def web?(contact_inbox)
    contact_inbox.inbox&.channel_type == 'Channel::WebWidget'
  end

  def started?(contact_inbox)
    contact_inbox.source_id.to_s.match?(STARTED)
  end

  def aged?(contact_inbox)
    contact_inbox.updated_at.nil? || contact_inbox.updated_at < LIFETIME.ago
  end

  # a verified session without the mark of a login, or a login session past its lifetime
  def stale?(contact_inbox)
    return aged?(contact_inbox) && web?(contact_inbox) if started?(contact_inbox)
    return false unless contact_inbox[:hmac_verified] == true || contact_inbox.source_id.to_s.start_with?('id-')

    web?(contact_inbox)
  end

  # each login renews the lifetime of its session
  def touch!(contact_inbox)
    contact_inbox.update_columns(updated_at: Time.current)
  end

  # the latest login session of this address in this inbox, within its lifetime
  def resumable(inbox, identifier, except)
    inbox.contact_inboxes.joins(:contact).where(contacts: { identifier: identifier })
         .where('contact_inboxes.source_id LIKE ?', "#{ChartContactGuard.binding_of(identifier)}s-%")
         .where('contact_inboxes.updated_at >= ?', LIFETIME.ago).where.not(id: except.id)
         .order(updated_at: :desc).first
  end

  def ticket(contact_inbox, until_at = Time.current.to_i + TICKET_SECONDS)
    key = Rails.application.key_generator.generate_key('chart-login-ticket')
    "#{until_at}.#{OpenSSL::HMAC.hexdigest('SHA256', key, "#{contact_inbox.source_id}:#{until_at}")}"
  end

  def ticket_ok?(value, contact_inbox)
    value = value.to_s
    return false unless value.match?(TICKET)

    until_at = value.split('.', 2).first.to_i
    now = Time.current.to_i
    return false unless until_at >= now - SKEW_SECONDS && until_at <= now + TICKET_SECONDS + SKEW_SECONDS

    ActiveSupport::SecurityUtils.secure_compare(value, ticket(contact_inbox, until_at))
  end

  # ends a session with its auth and realtime token; its lifetime runs on from its last login
  def revoke!(contact_inbox)
    kept = contact_inbox.source_id.to_s[/\Aid-[0-9a-f]{32}-(?:s-)?/].to_s
    contact_inbox.update_columns(source_id: "#{kept}#{SecureRandom.uuid}",
                                 pubsub_token: ContactInbox.generate_unique_secure_token)
  end

  # at the top of the widget's page, after ChartWidgetPage::SCRIPT
  # widget script begins
  WIDGET = <<~'JS'.gsub('%PAGE%', PAGE).gsub('%RESUME%', RESUME).gsub('%ENDED%', ENDED).gsub('%LOGIN%', LOGIN).freeze
    <script>(function () {
      var PREFIX = 'chatwoot-widget:', API = '/api/v1/widget/', LOGIN = API + 'contact/set_user';
      var UNSENT = 'chartUnsent';
      // a page that the login of this load opened
      var VERIFIED = %VERIFIED%;
      var embedded = window.parent !== window;
      var query = new URLSearchParams(location.search);
      var binding = false, leaving = false, wait = null, mounted = false, early = null, sending = '';
      var opened = query.has('chart_open'), reveal = opened;

      function post(message) { window.parent.postMessage(PREFIX + JSON.stringify(message), '*'); }
      function hold(on) {
        document.documentElement.inert = on;
        document.documentElement.style.opacity = on ? '0.5' : '';
        document.documentElement.style.pointerEvents = on ? 'none' : '';
        if (!on) { restore(); }
      }
      function tell(message) {
        var event = new MessageEvent('message', { data: PREFIX + JSON.stringify(message), source: window.parent });
        event.chartReplay = true;
        window.dispatchEvent(event);
      }
      // what the embedding page says when the chat opens: a reloaded or re-bound widget missed it
      function showOpen() {
        setTimeout(function () {
          if (opened && !leaving) { tell({ event: 'toggle-open', isOpen: true }); }
        }, 50);
      }
      // the text of a message that an ended session did not take: back in the composer, not sent
      function keep() {
        var box = document.querySelector('textarea#chat-input');
        var text = sending || (box && box.value) || '';
        if (!text) { return; }
        try { sessionStorage.setItem(UNSENT, JSON.stringify({ text: text, at: Date.now() })); } catch (e) { /* no storage */ }
      }
      function restore() {
        var saved = null;
        if (!query.has('chart_ended')) { return; }
        try { saved = JSON.parse(sessionStorage.getItem(UNSENT) || 'null'); } catch (e) { return; }
        if (!saved || typeof saved.text !== 'string' || Date.now() - saved.at > 60000) { return; }
        var tries = 0;
        (function put() {
          var box = document.querySelector('textarea#chat-input, form textarea');
          if (!box) {
            if (++tries < 120) { setTimeout(put, 500); }
            return;
          }
          try { sessionStorage.removeItem(UNSENT); } catch (e) { /* no storage */ }
          if (box.value) { return; }
          box.value = saved.text;
          box.dispatchEvent(new Event('input', { bubbles: true }));
        })();
      }
      function load(token, why, ticket) {
        leaving = true;
        if (why === 'chart_ended') { keep(); }
        hold(true);
        var next = new URLSearchParams(location.search);
        ['cw_conversation', 'chart_refused', 'chart_failed', 'chart_ended', 'chart_open', 'chart_login', 'chart_unbound', 'chart_resumed'].forEach(function (k) { next.delete(k); });
        if (why) { next.set(why, '1'); }
        if (opened) { next.set('chart_open', '1'); }
        if (token) { next.set('cw_conversation', token); }
        if (token && ticket) { next.set('chart_login', ticket); }
        location.replace(location.pathname.replace(/^\/+/, '/') + '?' + next.toString());
      }

      window.addEventListener('message', function (e) {
        if (!embedded || e.chartReplay || e.source !== window.parent) { return; }
        if (typeof e.data !== 'string' || e.data.indexOf(PREFIX) !== 0) { return; }
        var message;
        try { message = JSON.parse(e.data.slice(PREFIX.length)); } catch (err) { return; }
        if (message.event === 'toggle-open') { opened = !!message.isOpen; }
        var login = message.event === 'set-user' && message.identifier && message.user;
        // a resume that came up without its login once: this load stays anonymous
        if (login && query.has('chart_unbound')) {
          e.stopImmediatePropagation();
          return;
        }
        if (login) {
          binding = true;
          clearTimeout(wait);
          hold(true);
          if (mounted) { early = null; return; }
          // the app does not listen yet
          early = message;
          e.stopImmediatePropagation();
        } else if (message.event === 'config-set') {
          // the answer to the app's loaded: it listens from here on
          mounted = true;
          if (opened) { showOpen(); }
          if (early) {
            // after this message, unless the site gives the login again at once
            setTimeout(function () {
              var kept = early;
              early = null;
              if (kept && !leaving) { tell(kept); }
            }, 100);
          } else if (!binding) {
            // the site's setUser follows this message at once when there is a login
            clearTimeout(wait);
            wait = setTimeout(function () { if (!binding && !leaving) { hold(false); } }, 500);
          }
        }
      }, true);

      var open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (method, url) {
        var path = String(url), verb = String(method).toUpperCase(), result = open.apply(this, arguments);
        if (path.indexOf(API) < 0) { return result; }
        var login = path.indexOf(LOGIN) >= 0, xhr = this;
        this.chartWrites = verb === 'POST' && /\/(messages|conversations)([.?]|$)/.test(path);
        if (login) { this.setRequestHeader('%PAGE%', '2'); }
        this.addEventListener('loadend', function () {
          if (leaving) { return; }
          if (xhr.chartWrites && xhr.status >= 200 && xhr.status < 300) { sending = ''; }
          if (reveal && verb === 'GET' && path.indexOf(API + 'messages') >= 0) {
            // the history is there now: an open chat shows it with its composer
            reveal = false;
            showOpen();
          }
          if (login) {
            if (xhr.status === 200 && xhr.getResponseHeader('%RESUME%')) {
              // the page of a resume came up without its login (ticket lost or refused): once more
              // anonymously, and no login on that load, so a lost ticket never loops
              if (!VERIFIED && (query.has('chart_resumed') || query.has('chart_unbound'))) {
                if (!query.has('chart_unbound')) { return load(null, 'chart_unbound'); }
                binding = false;
                return hold(false);
              }
              var token = JSON.parse(xhr.responseText).widget_auth_token;
              post({ event: 'setAuthCookie', data: { widgetAuthToken: token } });
              return load(token, 'chart_resumed', xhr.getResponseHeader('%LOGIN%'));
            }
            // a failed login never leaves the visitor on the session it started from
            if (xhr.status !== 200 && !query.has('chart_failed')) { return load(null, 'chart_failed'); }
            binding = false;
            reveal = true;
            hold(false);
          } else if (xhr.status >= 400 && xhr.getResponseHeader('%ENDED%') && !query.has('chart_ended')) {
            load(null, 'chart_ended');
          }
        });
        return result;
      };
      XMLHttpRequest.prototype.send = function (body) {
        if (this.chartWrites && typeof body === 'string') {
          try {
            var content = (JSON.parse(body).message || {}).content;
            if (typeof content === 'string') { sending = content; }
          } catch (e) { /* no text */ }
        }
        return send.apply(this, arguments);
      };

      if (embedded) {
        hold(true);
        // the widget reports a failed login to the SDK, except on the page its login opened
        if (!VERIFIED) { post({ event: 'error', errorType: 'SET_USER_ERROR', data: { error: 'the login binds at every load' } }); }
      }
    })();</script>
  JS
  # widget script ends

  module Verified
    def hmac_verified
      super && ((ChartSessionGuard.started?(self) && !ChartSessionGuard.aged?(self)) || !ChartSessionGuard.web?(self))
    end

    def hmac_verified?
      hmac_verified
    end
  end

  # every path of a widget token but the login
  module Token
    private

    def set_contact
      super
      return unless ChartSessionGuard.stale?(@contact_inbox)
      return if controller_name == 'contacts' && action_name == 'set_user'

      response.set_header(ENDED, '1')
      render json: { error: 'This chat session ended. Reload the page.' }, status: :unauthorized
    rescue ActiveRecord::RecordNotFound
      response.set_header(ENDED, '1')
      raise
    end
  end

  # the widget's page: a login session only with the ticket of this load's login
  module Page
    private

    def set_contact
      super
      return unless @contact_inbox && ChartSessionGuard.started?(@contact_inbox)

      @chart_verified = ChartSessionGuard.ticket_ok?(params[:chart_login], @contact_inbox)
      @contact_inbox = @contact = nil unless @chart_verified
    end
  end

  # the two paths that hand out a token: a fresh anonymous session instead
  module Renew
    private

    def set_contact
      super
      @contact_inbox = @contact = nil if @contact_inbox && ChartSessionGuard.stale?(@contact_inbox)
    end
  end

  module Login
    def set_user
      id = params[:identifier].to_s
      return super if id.blank?

      current = @contact_inbox
      # old page check begins
      # a login needs the current page script: without it the request gets 409
      if request.headers[PAGE] != '2'
        ChartSessionGuard.revoke!(current)
        response.set_header(ENDED, '1')
        return render json: { error: 'Reload the page to log in to the chat.' }, status: :conflict
      end
      # old page check ends

      ActiveRecord::Base.transaction do
        current.lock!
        @contact = current.contact.reload
        same = ChartSessionGuard.started?(current) && !ChartSessionGuard.aged?(current) &&
               @contact.identifier == id && ChartContactGuard.bound?(current, id)
        unless same
          prior = ChartSessionGuard.resumable(@web_widget.inbox, id, current)
          @contact_inbox, = prior ? [prior] : build_contact_inbox_with_token(@web_widget)
          @contact = @contact_inbox.contact
        end
        chart_identify(@contact, id)
        @contact_inbox.reload
        chart_bind(id)
        ChartSessionGuard.touch!(@contact_inbox)
        unless same
          ChartSessionGuard.revoke!(current)
          payload = { source_id: @contact_inbox.source_id, inbox_id: @web_widget.inbox.id }
          @widget_auth_token ||= ::Widget::TokenService.new(payload: payload).generate_token
          response.set_header(RESUME, '1')
          response.set_header(LOGIN, ChartSessionGuard.ticket(@contact_inbox))
        end
      end
    end

    private

    def chart_bind(id)
      raise ActiveRecord::RecordInvalid, @contact_inbox unless @contact_inbox.contact.identifier == id

      changes = { hmac_verified: true }
      fresh = !(ChartSessionGuard.started?(@contact_inbox) && ChartContactGuard.bound?(@contact_inbox, id))
      changes[:source_id] = "#{ChartContactGuard.binding_of(id)}s-#{SecureRandom.uuid}" if fresh
      @contact_inbox.update!(changes)
      return unless fresh

      payload = { source_id: @contact_inbox.source_id, inbox_id: @web_widget.inbox.id }
      @widget_auth_token = ::Widget::TokenService.new(payload: payload).generate_token
    end
  end

  module CableJob
    private

    def broadcast_to_members(members, event_name, data)
      stale = ContactInbox.includes(:inbox).where(pubsub_token: members).select { |ci| ChartSessionGuard.stale?(ci) }
      super(members - stale.map { |ci| ci[:pubsub_token] }, event_name, data)
    end
  end

  module Room
    def subscribed
      if params[:user_id].blank?
        session = ContactInbox.find_by(pubsub_token: params[:pubsub_token].to_s)
        return reject if session.nil?

        if ChartSessionGuard.stale?(session)
          chart_notice(session)
          return reject
        end
      end
      super
    end

    private

    # a refused socket shows the reload line, with nothing of the conversation's content
    def chart_notice(session)
      chat = session.conversations.last
      return if chat.nil?

      now = Time.current.to_i
      transmit({ event: 'message.created',
                 data: { id: now, content: NOTICE, message_type: 1, content_type: 'text', content_attributes: {},
                         created_at: now, conversation_id: chat.display_id, status: 'sent' } })
    end
  end
end

# The super admin pages carry the SDK guard themselves.
module ChartAdminPage
  private

  def process_action(*)
    super
    return unless response.media_type == 'text/html'

    response.body = response.body.to_s.sub('<head>') { "<head><script>#{ChartSdkGuard::SCRIPT}</script>" }
  end
end

# the session token and the page's ticket stay out of the logs
Rails.application.config.filter_parameters += %i[cw_conversation chart_login identifier_hash]

Rails.application.config.to_prepare do
  ContactInbox.prepend ChartSessionGuard::Verified
  ActionCableBroadcastJob.prepend ChartSessionGuard::CableJob
  RoomChannel.prepend ChartSessionGuard::Room
  Api::V1::Widget::BaseController.prepend ChartSessionGuard::Token
  Api::V1::Widget::DirectUploadsController.prepend ChartSessionGuard::Token
  Api::V1::Widget::ConfigsController.prepend ChartSessionGuard::Renew
  Api::V1::Widget::ContactsController.prepend ChartSessionGuard::Login
  SuperAdmin::ApplicationController.prepend ChartAdminPage

  WidgetsController.class_eval do
    prepend ChartSessionGuard::Renew
    prepend ChartSessionGuard::Page
    after_action :chart_session_script

    private

    # inside ChartWidgetPage::Response, which gives the script its nonce
    def chart_session_script
      return unless response.successful? && response.media_type == 'text/html'

      response.set_header('Cache-Control', 'no-store')
      script = ChartSessionGuard::WIDGET.sub('%VERIFIED%', @chart_verified ? 'true' : 'false')
      response.body = response.body.sub('<head>') { "<head>#{script}" }
    end
  end
end
