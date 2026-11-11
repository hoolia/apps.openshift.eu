# Blocks until Chatwoot may serve; rails runner, in an init container.
#
# /health answers long before the database is usable, so it cannot gate the pods.
# CHATWOOT_WAIT_FOR=database (the prepare Job): the database answers and has pgvector.
# Otherwise (web and worker) also: no migration is pending, the account of the chart
# exists, and the onboarding page that makes its first visitor super admin is closed.
# bootstrap.rb closes it right after it creates the account, so a web pod never serves
# while that page is open. Gives up after CHATWOOT_WAIT_SECONDS (default 900).
want = ENV.fetch('CHATWOOT_WAIT_FOR', 'ready')
account_id = Integer(ENV.fetch('CHATWOOT_ACCOUNT_ID', '1'))
deadline = Time.now + Integer(ENV.fetch('CHATWOOT_WAIT_SECONDS', '900'))
loop do
  begin
    pool = ActiveRecord::Base.connection_pool
    state =
      if !pool.with_connection { |c| c.extension_enabled?('vector') }
        'waiting for pgvector'
      elsif want == 'database'
        nil
      elsif pool.migration_context.needs_migration?
        'waiting for migrations'
      elsif !Account.exists?(id: account_id)
        'waiting for the account'
      elsif Redis::Alfred.get(Redis::Alfred::CHATWOOT_INSTALLATION_ONBOARDING)
        'waiting for the onboarding page to close'
      end
    if state.nil?
      puts(want == 'database' ? 'database ready' : 'ready')
      break
    end
    puts state
  rescue StandardError => e
    puts "waiting for the database: #{e.class}"
    ActiveRecord::Base.connection_handler.clear_all_connections!
  end
  abort 'not ready in time' if Time.now > deadline
  sleep 5
end
