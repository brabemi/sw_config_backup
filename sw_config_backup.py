#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:set sw=4 ts=4 et:

import pexpect
import threading
import os
import shutil
import logging
import time
import configparser
import ast

def backup (switch, server):
	try:
		ssh=pexpect.spawn('ssh -o StrictHostKeyChecking=no %s@%s' % (switch['username'], switch['ip']))
		#~ print('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
		logging.debug('%s: connecting to ip: %s' % (switch['name'], switch['ip']))
		ssh.expect('password')
	except: 
		#~ print("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
		logging.error("Connection failed(%s)\n \t%s" % (switch['name'], ssh.before))
		return 1
	try:
		ssh.sendline('%s' % switch['password'])
		#~ print('%s: authenticating username: %s' % (switch['name'], switch['username']))
		logging.debug('%s: authenticating username: %s' % (switch['name'], switch['username']))
		ssh.expect('login')
	except: 
		#~ print("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
		logging.error("Authorization failed(%s)\n \tusername: %s" % (switch['name'], switch['username']))
		return 2
	try:
		ssh.sendline("backup fabric current-configuration to %s %s.cfg" % (server, switch['name']))
		#~ print('%s: backuping to server: %s' % (switch['name'], server))
		logging.debug('%s: backuping to server: %s' % (switch['name'], server))
		ssh.expect('finished!\s+<.*>',timeout=30)
		ssh.sendline('quit')
	except: 
		#~ print("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
		logging.error("Backup failed(%s)\n \t%s" % (switch['name'], ssh.before))
		return 3
	#~ print("Done %s" % switch['name'])
	#~ print("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
	logging.info("Configuration from %s uploaded to tftp server %s" % (switch['name'], server))
	return 0

def sws_cfg_check(sws_cfg):
	keys = {'username', 'password', 'name', 'ip', 'units'}
	for section in sws_cfg:
		for key in keys:
			if not key in sws_cfg[section]:
				raise Exception("Key \'%s\' in switches configuration in section \'%s\' is missing" % (key, section))

def load_switches_cfg():
	sws_cfg = configparser.ConfigParser()
	sws_cfg.read("conf/switches.cfg")
	retval = dict()
	for section in sws_cfg.sections():
		retval[section] = dict(sws_cfg.items(section))
	sws_cfg_check(retval)
	return retval

def app_cfg_check(app_cfg):
	keys = {'backup_dir_path', 'backup_server', 'file_expiration_timeout', 'tftp_dir_path', 'log_file'}
	for key in keys:
		if not key in app_cfg:
			raise Exception("Key \'%s\' in application configuration file is missing" % (key))

def load_app_cfg():
	app_cfg = configparser.ConfigParser()
	app_cfg.read("conf/app.cfg")
	retval = dict(app_cfg.items('APP'))
	app_cfg_check(retval)
	return retval

def main():
	app_cfg = load_app_cfg()
	logging.basicConfig(filename=app_cfg['log_file'], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
	switches_cfg = load_switches_cfg()

	threads = []
	for switch in switches_cfg:
		t = threading.Thread(target = backup, args = (switches_cfg[switch], app_cfg['backup_server']))
		t.start()
		threads.append(t)

	for t in threads:
		t.join()

	end_time = time.time()
	file_expiration_timeout = int(app_cfg['file_expiration_timeout'])
	for section in switches_cfg:
		switch = switches_cfg[section]
		units = ast.literal_eval(switch['units'])
		for unit in units:
			tmp_file_path = "%s/%s_%d.cfg" % (app_cfg['tftp_dir_path'],switch['name'],unit)
			if not os.access(tmp_file_path, os.R_OK):
				#~ print("Fail to read %s unit %d, expected file %s" % (switch['name'],unit,tmp_file_path))
				logging.warning("Fail to read %s unit %d, expected file %s" % (switch['name'],unit,tmp_file_path))
			elif (end_time - os.stat(tmp_file_path).st_mtime) > file_expiration_timeout:
				#~ print("Configuration of %s unit %d, file %s is older than %d s, file will be ignored" % (switch['name'],unit,tmp_file_path,file_expiration_timeout))
				logging.error("Configuration of %s unit %d, file %s is older than %d s, file will be ignored" % (switch['name'],unit,tmp_file_path, file_expiration_timeout))
			else:
				shutil.copy2(tmp_file_path, app_cfg['backup_dir_path'])
				logging.info("Saved %s unit %d configuration" % (switch['name'],unit))
	return 0

if __name__ == '__main__':
	main()

