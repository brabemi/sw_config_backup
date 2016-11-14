from app import app as application, load_app_cfg

if __name__ == "__main__":
	#~ app_cfg = load_app_cfg()
	#~ application.config['app_config'] = app_cfg
	application.run(debug=True)
