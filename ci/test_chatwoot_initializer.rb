# Tests of charts/chatwoot/files/chart_initializer.rb: the dashboard session lifetime.
#
# ruby ci/test_chatwoot_initializer.rb
require 'minitest/autorun'

INITIALIZER = File.expand_path('../charts/chatwoot/files/chart_initializer.rb', __dir__)

# the parts of Rails and devise_token_auth the initializer touches
class Integer
  def hours = self * 3600
end

module DeviseTokenAuth
  class << self
    attr_accessor :token_lifespan
  end
end

module Rails
  Config = Struct.new(:unused) do
    def to_prepare(&_block) = nil
  end
  Application = Struct.new(:config)

  def self.application
    @application ||= Application.new(Config.new)
  end
end

class SessionHoursTest < Minitest::Test
  CHATWOOT_DEFAULT = 2 * 30 * 24 * 3600

  def setup
    @env = ENV.to_h
    ENV['SMTP_ADDRESS'] = 'smtp.example.com'
    ENV.delete('ACTIVE_STORAGE_SERVICE')
    ENV.delete('CHATWOOT_SESSION_HOURS')
    DeviseTokenAuth.token_lifespan = CHATWOOT_DEFAULT
  end

  def teardown
    ENV.replace(@env)
  end

  def run_initializer(hours)
    ENV['CHATWOOT_SESSION_HOURS'] = hours unless hours.nil?
    load INITIALIZER
  end

  def test_unset_keeps_chatwoot_default
    run_initializer(nil)
    assert_equal CHATWOOT_DEFAULT, DeviseTokenAuth.token_lifespan
  end

  def test_chart_default_of_36_hours
    run_initializer('36')
    assert_equal 36 * 3600, DeviseTokenAuth.token_lifespan
  end

  def test_bounds_are_accepted
    run_initializer('25')
    assert_equal 25 * 3600, DeviseTokenAuth.token_lifespan
    run_initializer('720')
    assert_equal 720 * 3600, DeviseTokenAuth.token_lifespan
  end

  # below 25 hours the dashboard session cookie expires at once and every login loops
  def test_short_or_malformed_values_stop_the_start
    ['12', '24', '0', '721', '', '36h', ' 36', '025', '-30', '1e2'].each do |hours|
      DeviseTokenAuth.token_lifespan = CHATWOOT_DEFAULT
      error = assert_raises(ArgumentError, "#{hours.inspect} accepted") { run_initializer(hours) }
      assert_match(/CHATWOOT_SESSION_HOURS must be a whole number from 25 to 720/, error.message)
      assert_equal CHATWOOT_DEFAULT, DeviseTokenAuth.token_lifespan, "#{hours.inspect} changed the lifespan"
    end
  end
end
