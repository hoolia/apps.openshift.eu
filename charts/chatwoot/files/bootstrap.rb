# First-run setup of a Chatwoot instance; rails runner, after db:chatwoot_prepare.
#
# Safe to run on every install and upgrade. It
# - refuses to continue without pgvector
# - switches public signup and account creation off in the database, where Chatwoot
#   reads them after the first start (the environment only seeds them once)
# - creates the account and its super admin when none exists, so the onboarding page
#   that lets the first visitor become super admin never opens
# - with CHATWOOT_PLATFORM_TOKEN: keeps the Platform App of the login bridge with exactly
#   that token and gives it the account
# - with CHATWOOT_AGENT_BOT_TOKEN: keeps the Agent Bot with that access token, webhook
#   secret and URL, connected to exactly the inboxes named in CHATWOOT_AGENT_BOT_INBOXES
# - with CHATWOOT_AGENT_PANEL_PATH: keeps the Dashboard App of the agent panel at that path
#   of FRONTEND_URL
# It prints no credential.

conn = ActiveRecord::Base.connection
abort 'pgvector (extension vector) is not enabled in the database' unless conn.extension_enabled?('vector')

%w[ENABLE_ACCOUNT_SIGNUP CREATE_NEW_ACCOUNT_FROM_DASHBOARD].each do |name|
  config = InstallationConfig.find_or_initialize_by(name: name)
  next if config.persisted? && config.value == false

  config.value = false
  config.save!
  puts "#{name} switched off"
end
GlobalConfig.clear_cache

account_id = Integer(ENV.fetch('CHATWOOT_ACCOUNT_ID'))
account = Account.order(:id).first
if account.nil?
  _user, account = AccountBuilder.new(
    account_name: ENV.fetch('CHATWOOT_ACCOUNT_NAME'),
    email: ENV.fetch('CHATWOOT_ADMIN_EMAIL').strip.downcase,
    user_full_name: 'Administrator',
    user_password: ENV.fetch('CHATWOOT_ADMIN_PASSWORD'),
    super_admin: true,
    confirmed: true
  ).perform
  puts "account #{account.id} and its super admin created"
end
abort "the first account has id #{account.id}, the chart is set to #{account_id}" if account.id != account_id

# the page that makes the first visitor super admin
Redis::Alfred.delete(Redis::Alfred::CHATWOOT_INSTALLATION_ONBOARDING)

token = ENV.fetch('CHATWOOT_PLATFORM_TOKEN', '')
if token.empty?
  puts 'no platform token: login bridge not set up'
else
  abort 'platform token shorter than 32 characters' if token.length < 32
  app = PlatformApp.find_or_create_by!(name: ENV.fetch('CHATWOOT_PLATFORM_APP'))
  access = app.access_token || app.create_access_token
  access.reload
  unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, token)
    access.update!(token: token)
    puts "platform app #{app.name}: token set"
  end
  app.platform_app_permissibles.find_or_create_by!(permissible: account)
  puts "platform app #{app.name} may manage account #{account.id}"
  # a super admin is never the bridge's to log in (the chart initializer refuses it too)
  dropped = app.platform_app_permissibles.where(permissible_type: 'User', permissible_id: SuperAdmin.select(:id)).delete_all
  puts "platform app #{app.name}: #{dropped} super admin permission(s) removed" if dropped.positive?
end
bot_token = ENV.fetch('CHATWOOT_AGENT_BOT_TOKEN', '')
if bot_token.empty?
  puts 'no agent bot token: agent bot not set up'
else
  secret = ENV.fetch('CHATWOOT_AGENT_BOT_WEBHOOK_SECRET')
  abort 'agent bot token or webhook secret shorter than 32 characters' if bot_token.length < 32 || secret.length < 32
  bot = AgentBot.find_or_initialize_by(account_id: account.id, name: ENV.fetch('CHATWOOT_AGENT_BOT_NAME'))
  bot.outgoing_url = ENV.fetch('CHATWOOT_AGENT_BOT_URL')
  bot.description = 'Answers customers through an A2A agent until a person takes over (set by the chart)'
  bot.secret = secret unless bot.persisted? && ActiveSupport::SecurityUtils.secure_compare(bot.secret.to_s, secret)
  bot.save!
  access = bot.access_token || bot.create_access_token
  access.reload
  access.update!(token: bot_token) unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, bot_token)
  wanted = ENV.fetch('CHATWOOT_AGENT_BOT_INBOXES', '').split(',').map(&:strip).reject(&:empty?)
  account.inboxes.order(:id).each do |inbox|
    link = AgentBotInbox.find_by(inbox_id: inbox.id)
    if wanted.include?('*') || wanted.include?(inbox.name)
      link ||= AgentBotInbox.new(inbox: inbox)
      link.agent_bot = bot
      link.status = :active
      link.save!
      puts "agent bot #{bot.name}: inbox #{inbox.id} connected"
    elsif link && link.agent_bot_id == bot.id
      link.destroy!
      puts "agent bot #{bot.name}: inbox #{inbox.id} disconnected"
    end
  end
  missing = wanted - ['*'] - account.inboxes.pluck(:name)
  puts "agent bot #{bot.name}: no inbox named #{missing.join(', ')}" if missing.any?
end

panel_path = ENV.fetch('CHATWOOT_AGENT_PANEL_PATH', '')
if panel_path.empty?
  puts 'no agent panel path: agent panel not set up'
else
  panel_url = ENV.fetch('FRONTEND_URL').chomp('/') + panel_path
  title = ENV.fetch('CHATWOOT_AGENT_PANEL_TITLE')
  app = DashboardApp.find_or_initialize_by(account_id: account.id, title: title)
  app.user ||= account.administrators.order(:id).first
  app.content = [{ 'type' => 'frame', 'url' => panel_url }]
  app.save!
  puts "dashboard app #{title}: #{panel_url}"
end
puts 'bootstrap done'
