from collections import namedtuple
from datetime import datetime

import yaml

from duckietown_challenges.utils import indent, safe_yaml_dump
from . import dclogger
from .challenges_constants import ChallengesConstants
from .utils import raise_wrapped, check_isinstance, wrap_config_reader


class InvalidChallengeDescription(Exception):
    pass


# these are job statuses
STATE_START = 'START'
STATE_ERROR = 'ERROR'
STATE_SUCCESS = 'SUCCESS'
STATE_FAILED = 'FAILED'

ALLOWED_CONDITION_TRIGGERS = ChallengesConstants.ALLOWED_JOB_STATUS

allowed_permissions = ['snoop', 'change', 'moderate', 'grant']


class ChallengeStep(object):
    def __init__(self, name, title, description, evaluation_parameters,
                 features_required, timeout):
        self.name = name
        self.title = title
        self.description = description
        check_isinstance(evaluation_parameters, EvaluationParameters)
        self.evaluation_parameters = evaluation_parameters
        check_isinstance(features_required, dict)
        self.features_required = features_required
        self.timeout = timeout

    def as_dict(self):
        data = {}
        data['title'] = self.title
        data['description'] = self.description
        data['evaluation_parameters'] = self.evaluation_parameters.as_dict()
        data['features_required'] = self.features_required
        data['timeout'] = self.timeout
        return data

    @staticmethod
    @wrap_config_reader
    def from_yaml(data0, name):
        if not isinstance(data0, dict):
            msg = 'Need dict, got %s' % data0
            raise ValueError(msg)

        data = data0.copy()

        title = data.pop('title')
        description = data.pop('description')
        evaluation_parameters = EvaluationParameters.from_yaml(data.pop('evaluation_parameters'))
        features_required = data.pop('features_required')

        timeout = data.pop('timeout')

        if data:
            msg = 'Extra fields: %s' % list(data)
            raise ValueError(msg)

        return ChallengeStep(name, title, description, evaluation_parameters,
                             features_required, timeout=timeout)

    def update_image(self):
        self.evaluation_parameters.update_image()


SUBMISSION_CONTAINER_TAG = 'SUBMISSION_CONTAINER'


class EvaluationParameters(object):
    """
        You can specify these fields for the docker file:

            version: '3'

            services:
                evaluator:
                    image: imagename
                    environment:
                        var: var
                solution: # For the solution container
                    image: SUBMISSION_CONTAINER
                    environment:
                        var: var

    """

    def __init__(self, version, services):
        self.version = version
        self.services = services

    @staticmethod
    @wrap_config_reader
    def from_yaml(d0):
        if not isinstance(d0, dict):
            msg = 'Expected dict, got %s' % d0.__repr__()
            raise ValueError(msg)
        d = dict(**d0)
        services_ = d.pop('services')
        if not isinstance(services_, dict):
            msg = 'Expected dict got %s' % services_.__repr__()
            raise ValueError(msg)

        if not services_:
            msg = 'No services described.'
            raise ValueError(msg)

        version = d.pop('version', '3')

        services = {}
        for k, v in services_.items():
            services[k] = ServiceDefinition.from_yaml(v)

        if d:
            msg = 'Invalid fields %s' % list(d)
            raise ValueError(msg)

        # check that there is at least a service with the image called
        # SUBMISSION_CONTAINER
        n = 0
        for service_definition in services.values():
            if service_definition.image == SUBMISSION_CONTAINER_TAG:
                n += 1
        # if n == 0:
        #     msg = 'I expect one of the services to have "image: %s".' % SUBMISSION_CONTAINER_TAG
        #     raise ValueError(msg)
        # if n > 1:
        #     msg = 'Too many services with  "image: %s".' % SUBMISSION_CONTAINER_TAG
        #     raise ValueError(msg)

        return EvaluationParameters(services=services, version=version)

    def __repr__(self):
        return nice_repr(self)

    def as_dict(self):
        services = dict([(k, v.as_dict()) for k, v in self.services.items()])
        return dict(version=self.version, services=services)

    def equivalent(self, other):
        if set(other.services) != set(self.services):
            msg = 'Different set of services.'
            raise NotEquivalent(msg)
        for s in other.services:
            try:
                self.services[s].equivalent(other.services[s])
            except NotEquivalent as e:
                msg = 'Service %r differs:\n\n%s' % (s, indent(e, '  '))
                raise NotEquivalent(msg)


class NotEquivalent(Exception):
    pass


def nice_repr(x):
    K = type(x).__name__
    return '%s\n\n%s' % (K, indent(safe_yaml_dump(x.as_dict()), '   '))


class ServiceDefinition(object):
    def __init__(self, image, environment, image_digest, build):
        check_isinstance(environment, dict)
        check_isinstance(image, (unicode, str))
        self.image = str(image)
        self.image_digest = image_digest
        self.environment = environment
        self.build = build

    def __repr__(self):
        return nice_repr(self)

    def equivalent(self, other):
        if self.image != SUBMISSION_CONTAINER_TAG:
            if self.image_digest is None or other.image_digest is None:
                msg = 'No digest information, assuming different.\nself: %s\nother: %s' % (self, other)
                raise NotEquivalent(msg)
            if self.image_digest != other.image_digest:
                msg = 'Different digests:\n\n  %s\n\n  %s' % (self.image_digest, other.image_digest)
                raise NotEquivalent(msg)

        if self.environment != other.environment:
            msg = 'Different environments:\n\n %s\n\n  %s' % (self.environment, other.environment)
            raise NotEquivalent(msg)

    def update_image(self):
        self.image = get_latest(self.image)

    @staticmethod
    @wrap_config_reader
    def from_yaml(d0):
        if not isinstance(d0, dict):
            msg = 'Expected dict, got %s' % d0.__repr__()
            raise ValueError(msg)
        d = dict(**d0)
        if 'image' in d:
            image = d.pop('image')
        elif 'container' in d:
            image = d.pop('container')
        else:
            msg = 'Need parameter "image": %s' % d0
            raise ValueError(msg)
        environment = d.pop('environment', {})
        if environment is None:
            environment = {}

        if 'build' in d:
            build = d.pop('build')
            if build is not None:
                build = Build.from_yaml(build)
        else:
            build = None
        image_digest = d.pop('image_digest', None)
        if d:
            msg = 'Extra fields: %s' % list(d0)
            raise ValueError(msg)
        return ServiceDefinition(image, environment, image_digest, build)

    def __repr__(self):
        return nice_repr(self)

    def as_dict(self):

        res = dict(image=self.image, environment=self.environment, image_digest=self.image_digest)

        if self.build:
            res['build'] = self.build.as_dict()
        else:
            pass

        return res


class Build(object):
    def __init__(self, context, dockerfile, args):
        self.context = context
        self.dockerfile = dockerfile
        self.args = args

    def __repr__(self):
        return nice_repr(self)

    def as_dict(self):
        return dict(context=self.context, dockerfile=self.dockerfile, args=self.args)

    @staticmethod
    def from_yaml(d0):
        if not isinstance(d0, dict):
            msg = 'Expected dict, got %s' % d0.__repr__()
            raise ValueError(msg)
        d = dict(**d0)

        context = d.pop('context', '.')
        dockerfile = d.pop('dockerfile', 'Dockerfile')
        args = d.pop('args', {})

        if d:
            msg = 'Extra fields: %s' % list(d0)
            raise ValueError(msg)
        return Build(context, dockerfile, args)


def get_latest(image_name):
    if '@' in image_name:
        msg = 'The image %r already has a qualified hash. Not updating.' % image_name
        dclogger.warning(msg)
        return
    import docker
    client = docker.from_env()
    dclogger.info('Finding latest version of %s' % image_name)
    image = client.images.get(image_name)
    if ':' in image_name:
        # remove tag
        image_name_no_tag = image_name[:image_name.index(':')]
        image_name = image_name_no_tag

    fq = image_name + '@' + image.id
    dclogger.info('updated %s -> %r' % (image_name, fq))
    return fq


Transition = namedtuple('Transition', 'first condition second')


class InvalidSteps(Exception):
    pass


class ChallengeTransitions(object):
    def __init__(self, transitions, steps):
        self.transitions = []
        self.steps = steps
        for first, condition, second in transitions:
            assert first == STATE_START or first in self.steps, first
            assert second in [STATE_ERROR, STATE_FAILED, STATE_SUCCESS] or second in self.steps, second
            assert condition in ALLOWED_CONDITION_TRIGGERS, condition
            self.transitions.append(Transition(first, condition, second))

    def __repr__(self):
        return u"\n".join(self.steps_explanation())

    def steps_explanation(self):
        ts = []
        for first, condition, second in self.transitions:
            if first == STATE_START:
                ts.append('At the beginning execute step `%s`.' % second)
            else:
                if second in [STATE_ERROR, STATE_FAILED, STATE_SUCCESS]:
                    ts.append('If step `%s` finishes with status `%s`, then declare the submission `%s`.' %
                              (first, condition, second))
                else:
                    ts.append('If step `%s` finishes with status `%s`, then execute step `%s`.' %
                              (first, condition, second))
        return ts

    def get_next_steps(self, status):
        """ status is a dictionary from step ID to
            status.

            It contains at the beginning

                START: success

            Returns a list of steps.

            If the list is empty, then we are done

        """
        dclogger.info('Received status = %s' % status)
        assert isinstance(status, dict)
        assert STATE_START in status
        assert status[STATE_START] == 'success'
        status = dict(**status)
        for k, ks in list(status.items()):
            if k != STATE_START and k not in self.steps:
                msg = 'Ignoring invalid step %s -> %s' % (k, ks)
                dclogger.error(msg)
                status.pop(k)
            if ks not in ChallengesConstants.ALLOWED_JOB_STATUS:
                msg = 'Ignoring invalid step %s -> %s' % (k, ks)
                dclogger.error(msg)
                status.pop(k)

        to_activate = []
        for t in self.transitions:
            if t.first in status and status[t.first] == t.condition:
                dclogger.debug('Transition %s is activated' % str(t))

                like_it_does_not_exist = [ChallengesConstants.STATUS_ABORTED]
                if t.second in status and status[t.second] not in like_it_does_not_exist:
                    dclogger.debug('Second %s already activated (and in %s)' % (t.second, status[t.second]))
                else:
                    if t.second in [STATE_ERROR, STATE_FAILED, STATE_SUCCESS]:
                        dclogger.debug('Finishing here')
                        return True, t.second.lower(), []
                    else:

                        to_activate.append(t.second)

        dclogger.debug('Incomplete; need to do: %s' % to_activate)
        return False, None, to_activate


class Scoring(object):
    def __init__(self, scores):
        self.scores = scores

    def as_dict(self):
        scores = [_.as_dict() for _ in self.scores]
        return dict(scores=scores)


    def __repr__(self):
        return nice_repr(self)
    @staticmethod
    def from_yaml(data0):
        try:
            if not isinstance(data0, dict):
                msg = 'Expected dict, got %s' % type(data0).__name__
                raise InvalidChallengeDescription(msg)

            data = dict(**data0)
            scores = data.pop('scores')
            if not isinstance(scores, list):
                msg = 'Expected list, got %s' % type(scores).__name__
                raise InvalidChallengeDescription(msg)

            scores = [Score.from_yaml(_) for _ in scores]
            if data:
                msg = 'Extra keys in configuration file: %s' % list(data)
                raise InvalidChallengeDescription(msg)

            return Scoring(scores)

        except KeyError as e:
            msg = 'Missing config %s' % e
            raise_wrapped(InvalidChallengeDescription, e, msg)


class Score(object):
    def __init__(self, name, description, order):
        assert order in ['ascending', 'descending']
        self.name = name
        self.description = description
        self.order = order


    def __repr__(self):
        return nice_repr(self)
    def as_dict(self):
        return dict(description=self.description, name=self.name, order=self.order)

    @staticmethod
    def from_yaml(data0):
        try:
            if not isinstance(data0, dict):
                msg = 'Expected dict, got %s' % type(data0).__name__
                raise InvalidChallengeDescription(msg)

            data = dict(**data0)
            name = data.pop('name')
            description = data.pop('description', None)
            order = data.pop('order', 'ascending')

            if data:
                msg = 'Extra keys in configuration file: %s' % list(data)
                raise InvalidChallengeDescription(msg)

            return Score(name, description, order)
        except KeyError as e:
            msg = 'Missing config %s' % e
            raise_wrapped(InvalidChallengeDescription, e, msg)


class ChallengeDescription(object):
    def __init__(self, name, title, description, protocol,
                 date_open, date_close, steps, roles, transitions, tags, scoring):
        self.name = name
        self.title = title
        self.scoring = scoring
        self.description = description
        self.protocol = protocol
        self.date_open = date_open
        check_isinstance(date_open, datetime)
        check_isinstance(date_close, datetime)
        self.date_close = date_close
        self.steps = steps
        self.roles = roles
        self.tags = tags

        for k, permissions in self.roles.items():
            if not k.startswith('user:'):
                msg = 'Permissions should start with "user:", %s' % k
                raise InvalidChallengeDescription(msg)
            p2 = dict(**permissions)
            for perm in allowed_permissions:
                p2.pop(perm, None)
            if p2:
                msg = 'Unknown permissions: %s' % p2
                raise InvalidChallengeDescription(msg)

        self.first_step = None
        self.ct = ChallengeTransitions(transitions, list(self.steps))

    def get_steps(self):
        return self.steps

    def get_next_steps(self, status):
        return self.ct.get_next_steps(status)

    @staticmethod
    @wrap_config_reader
    def from_yaml(data):
        try:
            name = data.pop('challenge')
            tags = data.pop('tags', [])
            title = data.pop('title')
            description = data.pop('description')
            protocol = data.pop('protocol')
            date_open = data.pop('date-open')
            date_close = data.pop('date-close')

            roles = data.pop('roles')
            transitions = data.pop('transitions')
            steps = data.pop('steps')
            Steps = {}
            for k, v in steps.items():
                Steps[k] = ChallengeStep.from_yaml(v, k)

            scoring = Scoring.from_yaml(data.pop('scoring'))
            if data:
                msg = 'Extra keys in configuration file: %s' % list(data)
                raise InvalidChallengeDescription(msg)

            return ChallengeDescription(name, title, description,
                                        protocol, date_open, date_close, Steps,
                                        roles=roles, transitions=transitions,
                                        tags=tags, scoring=scoring)
        except KeyError as e:
            msg = 'Missing config %s' % e
            raise_wrapped(InvalidChallengeDescription, e, msg)

    def as_dict(self):
        data = {}
        data['challenge'] = self.name
        data['title'] = self.title
        data['description'] = self.description
        data['protocol'] = self.protocol
        data['date-open'] = self.date_open
        data['date-close'] = self.date_close
        data['roles'] = self.roles
        data['transitions'] = []
        for t in self.ct.transitions:
            tt = [t.first, t.condition, t.second]
            data['transitions'].append(tt)
        data['steps'] = {}
        for k, v in self.steps.items():
            data['steps'][k] = v.as_dict()
        data['tags'] = self.tags
        data['scoring'] = self.scoring.as_dict()
        return data

    def as_yaml(self):
        return yaml.dump(self.as_dict())
