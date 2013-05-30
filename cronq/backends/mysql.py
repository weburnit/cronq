from uuid import uuid4, UUID
import datetime
import os
import socket

import sqlalchemy
from sqlalchemy import (
    or_,
    Binary,
    Column,
    CHAR,
    DateTime,
    Integer,
    Interval,
    String,
    Time,
    Text,
)
from sqlalchemy.sql.expression import desc
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

Base = declarative_base()



class Storage(object):

    def __init__(self, publisher=None):
        self.publisher = publisher

        self._engine = new_engine()
        self._maker = sessionmaker(bind=self._engine)
        self.session = self._maker()

    def bootstrap(self):
        models = [
            Job,
            Event,
        ]
        for model in models:
            try:
                print 'Try model', model
                model.__table__.create(self._engine)
            except (OperationalError, ProgrammingError) as exc:
                print exc
                pass

    def close(self):
        self.session.close()
        self._engine.dispose()

    def add_job(self, name, interval_seconds, command, next_run=None, id=None):
        job = Job()
        job.id = id
        job.name = name
        job.interval = datetime.timedelta(seconds=interval_seconds)
        job.command = command
        self.session.merge(job)
        self.session.commit()

    def add_event(self, job_id, datetime, run_id, type, host, return_code):
        event = Event()
        event.job_id = job_id
        event.datetime = datetime
        event.run_id = run_id
        event.type = type
        event.host = host
        event.return_code = return_code
        self.session.add(event)
        self.session.commit()

    @property
    def jobs(self):
        session = self.session
        for job in session.query(Job):
            yield {
                'id': job.id,
                'name': job.name,
                'next_run': job.next_run,
                'interval': job.interval,
            }

    def get_job(self, id):
        job = self.session.query(Job).filter_by(id=id).first()
        return {
            'id': job.id,
            'name': job.name,
            'next_run': job.next_run,
            'interval': job.interval,
            'command': job.command,
        }

    def last_events_for_job(self, job_id, number):
        events = self.session.query(Event).filter_by(job_id=job_id).order_by(desc(Event.id)).limit(number)
        return event_models_to_docs(events)

    def events_for_run_id(self, run_id):
        events = self.session.query(Event).filter_by(run_id=run_id).order_by(Event.id)
        return event_models_to_docs(events)

    def failures(self):
        events = self.session.query(Event)\
            .filter_by(type='finished')\
            .filter(Event.return_code!=0)\
            .order_by(desc(Event.id)).limit(50)
        return event_models_to_docs(events)

    def run_job_now(self, id):
        event = self.session.query(Job).filter_by(id=id).first()
        event.run_now = True
        self.session.commit()

    def inject(self):
        """Get a message from storage and injects it into the job stream"""
        print 'Trying to inject', datetime.datetime.utcnow()
        while self.get_unpublished_task() is not None:
            pass

    def get_unpublished_task(self):
        session = self.session
        to_run = or_(
            Job.next_run < datetime.datetime.utcnow(),
            Job.run_now == True,
        )

        job = session.query(Job).filter(to_run).first()
        if job is None:
            return
        print 'Found a job:', job.name, job.next_run
        to_run_at = job.next_run
        while job.next_run < datetime.datetime.utcnow():
            print 'Adding time!'
            job.next_run += job.interval
        print job.next_run
        job_doc = {
            'name': job.name,
            'command': unicode(job.command),
            'id': job.id,
        }
        job.run_now = False
        me = '{0}.{1}'.format(socket.gethostname(), os.getpid())
        job.locked_by = me
        try:
            session.commit()
            self.publisher.publish(job.routing_key, job_doc, uuid4().hex)
        except Exception as exc:
            session.rollback()
            raise
        return True

def event_models_to_docs(events):
    for event in events:
        yield {
            'id': event.id,
            'datetime': event.datetime,
            'run_id': event.run_id,
            'host': event.host,
            'return_code': event.return_code,
            'type': event.type,
            'job_id': event.job_id,
        }


def new_engine():
    dsn = os.getenv('CRONQ_MYSQL', 'mysql+mysqlconnector://root@localhost/cronq')
    return create_engine(dsn, isolation_level='SERIALIZABLE')


class Job(Base):
    __tablename__ = 'jobs'

    id = Column(Integer, primary_key=True)
    name = Column(CHAR(255))
    interval = Column(Interval)
    next_run = Column(DateTime(), default=datetime.datetime.utcnow)
    routing_key = Column(CHAR(32), default='default')
    command = Column(Text())
    run_now = Column(Integer(1))
    locked_by = Column(CHAR(64))


class Event(Base):

    __tablename__ = 'events'

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer)
    datetime = Column(DateTime)
    run_id = Column(CHAR(32))
    type = Column(CHAR(32))
    host= Column(CHAR(255))
    return_code = Column(Integer)
