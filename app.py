from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
from sqlsoup import SQLSoup
from sqlalchemy.orm.exc import NoResultFound

from pprint import pprint

from flask import Flask
from flask_restful import reqparse, abort, Api, Resource

app = Flask(__name__)
api = Api(app)

engine = create_engine('sqlite:///data/sw_config_backup.sqlite', convert_unicode=True)
session = scoped_session(
    sessionmaker(autocommit=False, autoflush=False)
)
db = SQLSoup(engine, session=session)


def switch_to_dict(switch):
    tmp_switch = switch.__dict__
    tmp_switch.pop('_sa_instance_state')
    tmp_switch.pop('password')
    tmp_switch.pop('username')
    tmp_switch.pop('id')
    tmp_switch['units'] = [int(i) for i in tmp_switch['units'].split(',')]
    return tmp_switch


def get_sw_or_abort(sw_name):
    try:
        switch = db.switch.filter_by(name=sw_name).one()
    except NoResultFound:
        abort(404, message="Switch {} doesn't exist".format(sw_name))
    return switch


def get_sw_dict(sw_name):
    return switch_to_dict(get_sw_or_abort(sw_name))


class SwitchBackup(Resource):
    def get(self, sw_name):
        switch = get_sw_dict(sw_name)
        switch['backup'] = "now"
        return switch


class Switch(Resource):
    def get(self, sw_name):
        return get_sw_dict(sw_name)


class Switches(Resource):
    def get(self):
        data = []
        for switch in db.switch.all():
            data.append(switch_to_dict(switch))
        return data

#~ api.add_resource(Switches, '/')
api.add_resource(Switches, '/switch/')
api.add_resource(Switch, '/switch/<sw_name>/')
api.add_resource(SwitchBackup, '/switch/<sw_name>/backup')


if __name__ == '__main__':
    app.run(debug=True)
