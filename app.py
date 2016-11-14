import configparser
import sys
import pexpect
import time
import shutil
import subprocess
import os
import datetime

from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
from sqlsoup import SQLSoup
from sqlalchemy.orm.exc import NoResultFound

from flask import Flask
from flask_restful import abort, Api, Resource

from pprint import pprint


def switch_to_dict_all(switch):
	tmp_switch = switch.__dict__
	tmp_switch['units'] = [int(i) for i in tmp_switch['units'].split(',')]
	tmp_switch['last_backup'] = tmp_switch['last_backup'].__str__()
	return tmp_switch


def switch_to_dict_web(switch):
	tmp_switch = switch_to_dict_all(switch)
	tmp_switch.pop('_sa_instance_state')
	tmp_switch.pop('password')
	tmp_switch.pop('username')
	tmp_switch.pop('id')
	return tmp_switch


def get_sw_or_abort(sw_name):
	try:
		switch = db.switch.filter_by(name=sw_name).one()
	except NoResultFound:
		abort(404, message="Switch {} doesn't exist".format(sw_name))
	return switch


def backup(switch, server):
	if switch['type'].lower() == '3com':
		return backup_3com(switch, server)
	elif switch['type'].lower() == 'hp':
		return backup_hp(switch, server)
	else:
		app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
		return 4


def backup_3com(switch, server):
	try:
		ssh=pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
		app.logger.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
		ssh.expect('password')
	except: 
		app.logger.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
		return 1
	try:
		ssh.sendline('%s' % switch['password'])
		app.logger.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
		ssh.expect('login')
	except: 
		app.logger.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
		return 2
	try:
		ssh.sendline("backup fabric current-configuration to %s %s.cfg" % (server, switch['name']))
		app.logger.debug('%s: backuping to server: %s' % (switch['name'], server))
		ssh.expect('finished!\s+<.*>',timeout=30)
		ssh.sendline('quit')
	except: 
		app.logger.error("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
		return 3
	app.logger.info("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
	return 0


def backup_hp(switch, server):
	try:
		ssh=pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
		app.logger.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
		ssh.expect('password')
	except: 
		app.logger.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
		return 1
	try:
		ssh.sendline('%s' % switch['password'])
		app.logger.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
		ssh.expect('>')
	except: 
		app.logger.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
		return 2
	try:
		ssh.sendline("backup startup-configuration to %s %s.cfg" % (server, switch['name']))
		app.logger.debug('%s: backuping to server: %s' % (switch['name'], server))
		ssh.expect('finished!\s+<.*>',timeout=30)
		ssh.sendline('quit')
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
		tmp_file_path = "%s/%s_%d.cfg" % (app_cfg['tftp_dir_path'],switch['name'],unit)
		if not os.access(tmp_file_path, os.R_OK):
			app.logger.error("Fail to read %s unit %d, expected file %s" % (switch['name'],unit,tmp_file_path))
		elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
			app.logger.error("Configuration of %s unit %d, file %s is older than %d s, file will be ignored" % (switch['name'],unit,tmp_file_path, file_expiration_timeout))
		else:
			shutil.copy2(tmp_file_path, app_cfg['backup_dir_path'])
			app.logger.info("Saved %s unit %d configuration" % (switch['name'],unit))
			retval = 0
	return retval


def move_hp(app_cfg, switch):
	retval = 1

	end_time = time.time()
	file_expiration_timeout = int(app_cfg['file_expiration_timeout'])
	tmp_file_path = "%s/%s.cfg" % (app_cfg['tftp_dir_path'],switch['name'])

	if not os.access(tmp_file_path, os.R_OK):
		app.logger.error("Fail to read %s, expected file %s" % (switch['name'],tmp_file_path))
	elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
		app.logger.error("Configuration of %s, file %s is older than %d s, file will be ignored" % (switch['name'],tmp_file_path, file_expiration_timeout))
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
	else:
		app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
		return 1


def git_autocommit(app_cfg):
	command = "cd %s; git add -A; git commit -a -m 'autocommit on change'" % (app_cfg['backup_dir_path'])
	subprocess.Popen(command,stdout=subprocess.PIPE, shell=True)


def get_conf_3com(app_cfg, switch):
	retval = {}
	for unit in switch['units']:
		tmp_file_path = "%s/%s_%d.cfg" % (app_cfg['backup_dir_path'],switch['name'],unit)
		if not os.access(tmp_file_path, os.R_OK):
			#~ TODO: edit error message
			app.logger.error("Fail to read %s unit %d, expected file %s" % (switch['name'],unit,tmp_file_path))
			retval[unit] = None
		else:
			with open(tmp_file_path, 'rt') as config:
				retval[unit] = config.read()
	return retval


def get_conf_hp(app_cfg, switch):
	tmp_file_path = "%s/%s.cfg" % (app_cfg['backup_dir_path'],switch['name'])
	if not os.access(tmp_file_path, os.R_OK):
		#~ TODO: edit error message
		app.logger.error("Fail to read %s unit %d, expected file %s" % (switch['name'],unit,tmp_file_path))
		return None
	else:
		with open(tmp_file_path, 'rt') as config:
			return config.read()


def get_config(app_cfg, switch):
	if switch['type'].lower() == '3com':
		return get_conf_3com(app_cfg, switch)
	elif switch['type'].lower() == 'hp':
		return get_conf_hp(app_cfg, switch)
	else:
		app.logger.error("Unsupported type of switch (type: %s)" % (switch['type']))
		return 1


class SwitchBackup(Resource):
	def get(self, sw_name):
		db_switch = get_sw_or_abort(sw_name)
		if db_switch.backup_in_progress is True:
			return {'message': 'Backup in progress', 'last_backup': db_switch.last_backup.__str__()}
		db_switch.backup_in_progress = True
		db.commit()
		if db_switch.backup_in_progress == True:
			''' K objektu je potřeba přistoupit jinak nemá načtené položky a nelze z něj udělat dict '''
			pass
		try:
			switch = switch_to_dict_all(db_switch)
			if backup(switch, app.config['app_config']['backup_server']):
				db_switch.backup_in_progress = False
				db.commit()
				return {'message': 'Backup failed'}, 500
			if move_to_backup_folder(app.config['app_config'], switch):
				db_switch.backup_in_progress = False
				db.commit()
				return {'message': 'Backup failed'}, 500
			if app.config['app_config']['git_autocommit'] is True:
				git_autocommit(app.config['app_config'])
		except Exception as e:
			app.logger.error("Error during backuping {}".format(e))
			pprint(db_switch)
			pprint(db_switch.__dict__)
			db_switch.backup_in_progress = False
			db.commit()
			return {'message': 'Backup failed'}, 500
		db_switch.backup_in_progress = False
		db_switch.last_backup = datetime.datetime.now()
		db.commit()
		return {'message': 'Backup finished'}


class Switch(Resource):
	def get(self, sw_name):
		return switch_to_dict_web(get_sw_or_abort(sw_name))


class SwitchConfig(Resource):
	def get(self, sw_name):
		switch = get_sw_or_abort(sw_name)
		if switch.last_backup == None:
			return {'message': 'No config'}
		switch = switch_to_dict_web(switch)
		configs = get_config(app.config['app_config'], switch)
		return {'last_backup': switch['last_backup'], "configs": configs}


class Switches(Resource):
	def get(self):
		data = []
		for switch in db.switch.all():
			data.append(switch_to_dict_web(switch))
		return data


def app_cfg_check(app_cfg):
	keys = {'backup_dir_path', 'backup_server', 'file_expiration_timeout', 'tftp_dir_path', 'log_file', 'git_autocommit'}
	for key in keys:
		if not key in app_cfg:
			raise Exception("Key \'%s\' in application configuration file is missing" % (key))


def load_app_cfg():
	app_cfg = configparser.ConfigParser()
	app_cfg.read("%s/conf/app.cfg" % (sys.path[0]))
	retval = dict(app_cfg.items('APP'))
	app_cfg_check(retval)
	retval['git_autocommit'] = retval['git_autocommit'].lower() in ['true', '1', 'yes', 'y']
	return retval


app = Flask(__name__)
api = Api(app)

app_cfg = load_app_cfg()
app.config['app_config'] = app_cfg

engine = create_engine(app.config['app_config']['database'], convert_unicode=True)
session = scoped_session(
	sessionmaker(autocommit=False, autoflush=False)
)
db = SQLSoup(engine, session=session)


api.add_resource(Switches, '/', '/switch/')
api.add_resource(Switch, '/<string:sw_name>/', '/switch/<string:sw_name>/')
api.add_resource(SwitchBackup, '/<string:sw_name>/backup', '/switch/<string:sw_name>/backup')
api.add_resource(SwitchConfig, '/<string:sw_name>/config', '/switch/<string:sw_name>/config')


if __name__ == '__main__':
	app.run(debug=True)
