
LOGIN_ACCOUNT_REQUEST  = 10001001
LOGIN_ACCOUNT_RESPONSE = 11001001
LOGOUT_ACCOUNT_REQUEST  = 10001002
LOGOUT_ACCOUNT_RESPONSE = 11001002
QUERY_ORDERS_REQUEST  = 10003001
QUERY_ORDERS_RESPONSE = 11003001
QUERY_FILLS_REQUEST  = 10003002
QUERY_FILLS_RESPONSE = 11003002
QUERY_CAPITAL_REQUEST  = 10003003
QUERY_CAPITAL_RESPONSE = 11003003
QUERY_POSITION_REQUEST  = 10003004
QUERY_POSITION_RESPONSE = 11003004
QUERY_RATION_REQUEST  = 10005001
QUERY_RATION_RESPONSE = 11005001
PLACE_ORDER = 10002001
CANCEL_ORDER = 10002002
PLACE_BATCH_ORDER = 10002003
PLACE_REPORT = 20001001
FILL_REPORT = 20001002
CANCEL_REPORT = 20001003
CANCEL_RESPONSE = 11002002
PLACE_BATCH_RESPONSE = 11002003


#REQ_API_NAMES = {
#    LOGIN_ACCOUNT_REQUEST: 'handle_login_request',
#    LOGOUT_ACCOUNT_REQUEST: 'handle_logout_request',
#    QUERY_ORDERS_REQUEST: 'handle_query_order_request',
#    QUERY_FILLS_REQUEST: 'handle_query_trade_request',
#    QUERY_CAPITAL_REQUEST: 'handle_query_capital_request',
#    QUERY_POSITION_REQUEST: 'handle_query_position_request',
#    QUERY_RATION_REQUEST: 'handle_query_ration_request',
#    PLACE_ORDER: 'handle_send_order_request',
#    CANCEL_ORDER: 'handle_order_cancelation_request',
#    PLACE_BATCH_ORDER: 'handle_batch_order_request'
#}


REQ_API_NAMES = {
    LOGIN_ACCOUNT_REQUEST: 'handle_sync_request',
    LOGOUT_ACCOUNT_REQUEST: 'handle_sync_request',
    QUERY_ORDERS_REQUEST: 'handle_sync_request',
    QUERY_FILLS_REQUEST: 'handle_sync_request',
    QUERY_CAPITAL_REQUEST: 'handle_sync_request',
    QUERY_POSITION_REQUEST: 'handle_sync_request',
    QUERY_RATION_REQUEST: 'handle_sync_request',
    PLACE_ORDER: 'handle_async_request',
    CANCEL_ORDER: 'handle_async_request',
    PLACE_BATCH_ORDER: 'handle_async_request'
}


RSP_API_NAMES = {
    LOGIN_ACCOUNT_RESPONSE: 'on_login',
    LOGOUT_ACCOUNT_RESPONSE: 'on_logout',
    QUERY_ORDERS_RESPONSE: 'on_order_query',
    QUERY_FILLS_RESPONSE: 'on_trade_query',
    QUERY_CAPITAL_RESPONSE: 'on_capital_query',
    QUERY_POSITION_RESPONSE: 'on_position_query',
    QUERY_RATION_RESPONSE: 'on_ration_query',    
    PLACE_REPORT: 'on_order',
    FILL_REPORT: 'on_trade',
    CANCEL_REPORT: 'on_order_cancelation',    
    CANCEL_RESPONSE: 'on_order_cancelation_submission',
    PLACE_BATCH_RESPONSE: 'on_batch_order_submission'
}
