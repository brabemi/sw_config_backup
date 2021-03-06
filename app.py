import configparser
import sys
import pexpect
import time
import shutil
import subprocess
import os
import datetime
import queue
import threading
import signal
import logging
import re

import flask
from functools import wraps
# from flask import request, Response

from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
from sqlsoup import SQLSoup
from sqlalchemy.orm.exc import NoResultFound


def switch_to_dict_all(switch):
    tmp_switch = switch.__dict__
    tmp_switch['units'] = [int(i) for i in tmp_switch['units'].split(',')]
    tmp_switch['last_backup'] = str(tmp_switch['last_backup'])
    return tmp_switch


def switch_to_dict_web(switch):
    tmp_switch = switch_to_dict_all(switch)
    tmp_switch.pop('_sa_instance_state')
    tmp_switch.pop('password')
    tmp_switch.pop('username')
    tmp_switch.pop('id')
    return tmp_switch


def switch_to_dict_ser(switch):
    tmp_switch = switch_to_dict_all(switch)
    tmp_switch.pop('_sa_instance_state')
    return tmp_switch


def get_sw_or_abort(sw_name):
    try:
        switch = db.switch.filter_by(name=sw_name).one()
    except NoResultFound:
        flask.abort(404, flask.jsonify({'message': "Switch {} doesn't exist".format(sw_name)}))
    return switch


def backup(switch, server):
    if switch['type'].lower() == '3com':
        return backup_3com(switch, server)
    elif switch['type'].lower() == 'hp':
        return backup_hp(switch, server)
    elif switch['type'].lower() == 'aruba':
        return backup_aruba(switch, server)
    else:
        app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
        return 4


def backup_3com(switch, server):
    try:
        ssh = pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
        app.logger.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
        ssh.expect('password', timeout=60)
    except:
        app.logger.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 1
    try:
        ssh.sendline('%s' % switch['password'])
        app.logger.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
        ssh.expect('login', timeout=60)
    except:
        app.logger.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
        return 2
    try:
        ssh.sendline("backup fabric current-configuration to %s %s.cfg" % (server, switch['name']))
        app.logger.debug('%s: backuping to server: %s' % (switch['name'], server))
        ssh.expect('finished!\s+<.*>', timeout=60)
        ssh.sendline('quit')
    except:
        app.logger.error("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 3
    app.logger.info("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
    return 0


def backup_hp(switch, server):
    try:
        ssh = pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
        app.logger.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
        ssh.expect('password', timeout=60)
    except:
        app.logger.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 1
    try:
        ssh.sendline('%s' % switch['password'])
        app.logger.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
        ssh.expect('>', timeout=60)
    except:
        app.logger.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
        return 2
    try:
        ssh.sendline("backup startup-configuration to %s %s.cfg" % (server, switch['name']))
        app.logger.debug('%s: backuping to server: %s' % (switch['name'], server))
        ssh.expect('finished!\s+<.*>', timeout=60)
        ssh.sendline('quit')
    except:
        app.logger.error("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 3
    app.logger.info("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
    return 0


def backup_aruba(switch, server):
    try:
        ssh = pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
        app.logger.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
        ssh.expect('password', timeout=60)
    except:
        app.logger.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 1
    try:
        ssh.sendline('%s' % switch['password'])
        app.logger.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
        ssh.expect('to continue', timeout=60)
        ssh.sendline('')
        ssh.expect('#', timeout=60)
    except:
        app.logger.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
        return 2
    try:
        app.logger.debug('%s: backuping to server: %s' % (switch['name'], server))
        ssh.sendline('copy running-config tftp %s %s_rc.cfg' % (server, switch['name']))
        ssh.expect('#', timeout=60)
        ssh.sendline('copy startup-config tftp %s %s_sc.cfg' % (server, switch['name']))
        ssh.expect('#', timeout=60)
        ssh.sendline('no page')
        ssh.expect('#', timeout=60)
        delimiter = ssh.before[-8:] + b'#'
        ssh.sendline('show running-config structured')
        ssh.expect(delimiter, timeout=120)
        data = ssh.before
        ssh.sendline('quit')
        ssh.expect('>', timeout=60)
        ssh.sendline('quit')
        ssh.expect('(y/n)', timeout=60)
        ssh.sendline('y')
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        result = ansi_escape.sub('', data.decode("utf-8"))
        result = result.splitlines()[3:-2]
        file_path = "%s/%s_rc_struct.cfg" % (app_cfg['backup_dir_path'], switch['name'])
        with open(file_path, 'w') as file:
            file.writelines(l + '\n' for l in result)
    except:
        app.logger.error("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
        return 3
    app.logger.info("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
    
    return 0


def move_3com(app_cfg, switch):
    retval = 1

    end_time = time.time()
    file_expiration_timeout = int(app_cfg['file_expiration_timeout'])

    for unit in switch['units']:
        tmp_file_path = "%s/%s_%d.cfg" % (app_cfg['tftp_dir_path'], switch['name'], unit)
        if not os.access(tmp_file_path, os.R_OK):
            app.logger.error("Fail to read %s unit %d, expected file %s" % (switch['name'], unit, tmp_file_path))
        elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
            app.logger.error(
                "Configuration of %s unit %d, file %s is older than %d s, file will be ignored" %
                (switch['name'], unit, tmp_file_path, file_expiration_timeout)
            )
        else:
            shutil.copy2(tmp_file_path, app_cfg['backup_dir_path'])
            app.logger.info("Saved %s unit %d configuration" % (switch['name'], unit))
            retval = 0
    return retval


def move_hp(app_cfg, switch):
    retval = 1

    end_time = time.time()
    file_expiration_timeout = int(app_cfg['file_expiration_timeout'])
    tmp_file_path = "%s/%s.cfg" % (app_cfg['tftp_dir_path'], switch['name'])

    if not os.access(tmp_file_path, os.R_OK):
        app.logger.error("Fail to read %s, expected file %s" % (switch['name'], tmp_file_path))
    elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
        app.logger.error(
            "Configuration of %s, file %s is older than %d s, file will be ignored" %
            (switch['name'], tmp_file_path, file_expiration_timeout)
        )
    else:
        shutil.copy2(tmp_file_path, app_cfg['backup_dir_path'])
        app.logger.info("Saved %s configuration" % (switch['name']))
        retval = 0
    return retval


def move_aruba(app_cfg, switch):
    retval = 1

    end_time = time.time()
    file_expiration_timeout = int(app_cfg['file_expiration_timeout'])
    tmp_file_paths = [
        "%s/%s_rc.cfg" % (app_cfg['tftp_dir_path'], switch['name']),
        "%s/%s_sc.cfg" % (app_cfg['tftp_dir_path'], switch['name'])
    ]

    for tmp_file_path in tmp_file_paths:
        if not os.access(tmp_file_path, os.R_OK):
            app.logger.error("Fail to read %s, expected file %s" % (switch['name'], tmp_file_path))
        elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
            app.logger.error(
                "Configuration of %s, file %s is older than %d s, file will be ignored" %
                (switch['name'], tmp_file_path, file_expiration_timeout)
            )
        else:
            shutil.copy2(tmp_file_path, app_cfg['backup_dir_path'])
            app.logger.info("Saved %s configuration" % (switch['name']))
            retval = 0
    return retval


def move_to_backup_folder(app_cfg, switch):
    if switch['type'].lower() == '3com':
        return move_3com(app_cfg, switch)
    elif switch['type'].lower() == 'hp':
        return move_hp(app_cfg, switch)
    elif switch['type'].lower() == 'aruba':
        return move_aruba(app_cfg, switch)
    else:
        app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
        return 1


def git_autocommit(app_cfg):
    command = "cd %s; git add -A; git commit -a -m 'autocommit on change'" % (app_cfg['backup_dir_path'])
    subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)


def get_conf_3com(app_cfg, switch):
    retval = {}
    for unit in switch['units']:
        tmp_file_path = "%s/%s_%d.cfg" % (app_cfg['backup_dir_path'], switch['name'], unit)
        if not os.access(tmp_file_path, os.R_OK):
            app.logger.error("Fail to read %s unit %d, expected file %s" % (switch['name'], unit, tmp_file_path))
            retval[unit] = None
        else:
            with open(tmp_file_path, 'rt') as config:
                retval[unit] = config.read()
    return retval


def get_conf_hp(app_cfg, switch):
    tmp_file_path = "%s/%s.cfg" % (app_cfg['backup_dir_path'], switch['name'])
    if not os.access(tmp_file_path, os.R_OK):
        app.logger.error("Fail to read %s, expected file %s" % (switch['name'], tmp_file_path))
        return None
    else:
        with open(tmp_file_path, 'rt') as config:
            return {'all': config.read()}


def get_conf_aruba(app_cfg, switch):
    tmp_file_path = "%s/%s_rc_struct.cfg" % (app_cfg['backup_dir_path'], switch['name'])
    if not os.access(tmp_file_path, os.R_OK):
        app.logger.error("Fail to read %s, expected file %s" % (switch['name'], tmp_file_path))
        return None
    else:
        with open(tmp_file_path, 'rt') as config:
            return {'all': config.read()}


def get_config(app_cfg, switch):
    if switch['type'].lower() == '3com':
        return get_conf_3com(app_cfg, switch)
    elif switch['type'].lower() == 'hp':
        return get_conf_hp(app_cfg, switch)
    elif switch['type'].lower() == 'aruba':
        return get_conf_aruba(app_cfg, switch)
    else:
        app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
        return 1


def app_cfg_check(app_cfg):
    keys = {
        'backup_dir_path', 'backup_server', 'file_expiration_timeout', 'tftp_dir_path', 'log_file', 'log_level',
        'git_autocommit', 'database', 'worker_threads', 'username', 'password', 'key_path', 'crt_path'
    }
    for key in keys:
        if key not in app_cfg:
            raise Exception("Key \'%s\' in application configuration file is missing" % (key))


def load_app_cfg():
    app_cfg = configparser.ConfigParser()
    app_cfg.read("%s/conf/app.cfg" % (sys.path[0]))
    retval = dict(app_cfg.items('APP'))
    app_cfg_check(retval)
    retval['git_autocommit'] = retval['git_autocommit'].lower() in ['true', '1', 'yes', 'y']
    retval['worker_threads'] = int(retval['worker_threads'])
    log_levels = {
        'debug': logging.DEBUG, 'info': logging.INFO, 'warning': logging.WARNING,
        'error': logging.ERROR, 'critical': logging.CRITICAL
    }
    log_level = retval['log_level'].lower()
    retval['log_level'] = log_levels[log_level] if log_level in log_levels else logging.INFO
    return retval


def make_db_session(database):
    engine = create_engine(database, convert_unicode=True)
    session = scoped_session(
        sessionmaker(autocommit=False, autoflush=False)
    )
    return SQLSoup(engine, session=session)


def backup_task(switch):
    if backup(switch, app_cfg['backup_server']) != 0:
        # print('Fail during backup')
        return 1
    if move_to_backup_folder(app_cfg, switch) != 0:
        # print('Fail during moving')
        return 2
    if app_cfg['git_autocommit'] is True:
        git_autocommit(app_cfg)
    return 0


def worker():
    mydb = make_db_session(app_cfg['database'])
    while True:
        if program_closed:
            break
        switch_id = tasks.get()
        if switch_id is not None:
            db_switch = mydb.switch.get(switch_id)
            switch = switch_to_dict_all(db_switch)
            result = backup_task(switch)
            db_switch.backup_in_progress = False
            if result == 0:
                db_switch.last_backup = datetime.datetime.now()
            mydb.commit()
        tasks.task_done()
    return


def sigint_handler(signum, frame):
    global program_closed
    program_closed = True
    for thred in threads:
        tasks.put(None)
    app.logger.info('Waiting for the termination of the workers threads')
    for thred in threads:
        thred.join()
    while True:
        try:
            switch_id = tasks.get_nowait()
            if switch_id is not None:
                db_switch = db.switch.get(switch_id)
                db_switch.backup_in_progress = False
                db.commit()
            tasks.task_done()
        except queue.Empty:
            break
    sys.exit(0)

signal.signal(signal.SIGINT, sigint_handler)
signal.signal(signal.SIGTERM, sigint_handler)

app = flask.Flask(__name__)

app_cfg = load_app_cfg()
app.config['app_config'] = app_cfg

tasks = queue.Queue()
program_closed = False
threads = []
for i in range(app_cfg['worker_threads']):
    t = threading.Thread(target=worker)
    t.start()
    threads.append(t)

db = make_db_session(app.config['app_config']['database'])


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    return username == app_cfg['username'] and password == app_cfg['password']


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return flask.Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = flask.request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


@app.route('/', methods=['GET'])
@requires_auth
def get_all_switches():
    data = []
    for switch in db.switch.all():
        data.append(switch_to_dict_web(switch))
    return flask.jsonify(data)


@app.route('/backup-all/', methods=['GET'])
@requires_auth
def nonblocking_backup_all():
    for db_switch in db.switch.all():
        if db_switch.backup_in_progress is False:
            db_switch.backup_in_progress = True
            db.commit()
            try:
                if not program_closed:
                    tasks.put(db_switch.id)
            except Exception as e:
                app.logger.error("Error during backuping {}".format(e))
                db_switch.backup_in_progress = False
                db.commit()
    return flask.jsonify({'message': 'Backups added to queue'})


@app.route('/clear-all/', methods=['GET'])
@requires_auth
def clear_all():
    for db_switch in db.switch.all():
        if db_switch.backup_in_progress is True:
            db_switch.backup_in_progress = False
    db.commit()
    return flask.jsonify({'message': 'Statuses cleared'})


@app.route('/<string:sw_name>/', methods=['GET'])
@requires_auth
def get_switch(sw_name):
    # TODO: non existing name
    return flask.jsonify(switch_to_dict_web(get_sw_or_abort(sw_name)))


@app.route('/<string:sw_name>/config/', methods=['GET'])
@requires_auth
def get_switch_config(sw_name):
    switch = get_sw_or_abort(sw_name)
    if switch.last_backup is None:
        return flask.jsonify({'message': 'No config'})
    switch = switch_to_dict_web(switch)
    configs = get_config(app.config['app_config'], switch)
    return flask.jsonify({'last_backup': switch['last_backup'], "configs": configs})


@app.route('/<string:sw_name>/backup/', methods=['GET'])
@requires_auth
def nonblocking_backup(sw_name):
    db_switch = get_sw_or_abort(sw_name)
    if db_switch.backup_in_progress is True:
        return flask.jsonify({'message': 'Backup in progress', 'last_backup': str(db_switch.last_backup)})
    db_switch.backup_in_progress = True
    db.commit()
    try:
        if not program_closed:
            tasks.put(db_switch.id)
    except Exception as e:
        app.logger.error("Error during backuping {}".format(e))
        db_switch.backup_in_progress = False
        db.commit()
        return flask.jsonify({'message': 'Backup failed'}), 500
    return flask.jsonify({'message': 'Backup added to queue'})


@app.route('/<string:sw_name>/clear/', methods=['GET'])
@requires_auth
def clear(sw_name):
    db_switch = get_sw_or_abort(sw_name)
    if db_switch.backup_in_progress is True:
        db_switch.backup_in_progress = False
        db.commit()
    return flask.jsonify({'message': 'Status cleared'})


if __name__ == '__main__':
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')

    handler = logging.FileHandler(app.config['app_config']['log_file'])
    handler.setLevel(app.config['app_config']['log_level'])
    handler.setFormatter(formatter)

    handler2 = logging.StreamHandler()
    handler2.setLevel(app.config['app_config']['log_level'])
    handler2.setFormatter(formatter)

    app.logger.addHandler(handler)
    app.logger.addHandler(handler2)
    app.logger.setLevel(app.config['app_config']['log_level'])

    context = (app_cfg['crt_path'], app_cfg['key_path'])
    app.run(host='0.0.0.0', ssl_context=context)
