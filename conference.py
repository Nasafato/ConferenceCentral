#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'alanjgou@gmail.com (Alan Gou)'


from datetime import datetime
from datetime import date as datetime_date

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import SessionForm
from models import SessionForms
from models import SessionMiniForm
from models import Session
from models import SessionType
from models import SessionTimeQueryForm
from models import SessionTypeTimeForm

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

CONFERENCE_DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESSION_DEFAULTS = {
    "duration": 1,
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_CONFERENCE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

SESS_TYPE_REQUEST = endpoints.ResourceContainer(
    SessionMiniForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_SPEAKER_REQUEST = endpoints.ResourceContainer(
    speaker=messages.StringField(1),
)

SESS_TIME_REQUEST = endpoints.ResourceContainer(
    SessionTimeQueryForm,
    websafeConferenceKey=messages.StringField(1),
    conferenceDate=messages.StringField(2),
)


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in CONFERENCE_DEFAULTS:
            if data[df] in (None, []):
                data[df] = CONFERENCE_DEFAULTS[df]
                setattr(request, df, CONFERENCE_DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        return self._getConferencesToAttend(request)


    def _getConferencesToAttend(self, request, forms=True):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        if forms:
            # return set of ConferenceForm objects per Conference
            return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
                                          for conf in conferences])
        else:
            return conf_keys


    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(SESS_CONFERENCE_GET_REQUEST, SessionForms,
        path='getConferenceSessions',
        http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all the sessions of a conference"""
        conference_key = ndb.Key(urlsafe=request.websafeConferenceKey)

        return self._getConferenceSessions(conference_key)

    def _getConferenceSessions(self, conference_key, forms=True):
        """Returns sessions either as forms or as session objects"""
        if conference_key.kind() != 'Conference':
            raise endpoints.BadRequestException(
                'Key %s is not a valid conference key' % conference_key
            )

        sessions = Session.query(ancestor=conference_key)

        if forms:
            return SessionForms(
                items=[self._copySessionToForm(session) for session in sessions]
            )
        else:
            return sessions


    @endpoints.method(SESS_SPEAKER_REQUEST, SessionForms,
                      path='getSessionsBySpeaker',
                      http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Returns the session forms of all sessions with a given speaker"""
        sessions = Session.query().filter(Session.speaker == request.speaker)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        session_form = SessionForm()
        for field in session_form.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith(('date', 'Time')):
                    setattr(session_form, field.name, str(getattr(session, field.name)))
                elif field.name == 'typeOfSession':
                    setattr(session_form, field.name, getattr(SessionType, getattr(session, field.name)))
                else:
                    setattr(session_form, field.name, getattr(session, field.name))
            elif field.name == 'websafeSessionKey':
                setattr(session_form, field.name, session.key.urlsafe())
        session_form.check_initialized()
        return session_form


    @endpoints.method(SESS_POST_REQUEST, SessionForm,
                      path='createSession',
                      http_method='POST',name='createSession')
    def createSession(self, request):
        """Creates a Session for the Conference"""
        # make sure user is authed
        return self._createSessionObject(request)

    def _createSessionObject(self, request):
        """Create or update Session object, returning SessionForm/request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.websafeConferenceKey:
            raise endpoints.BadRequestException("Session 'websafeConferenceKey' field required")

        conference_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if conference_key.kind() != 'Conference':
            raise endpoints.BadRequestException(
                'Given key is not a conference key: %s' % request.websafeConferenceKey)

        conference = conference_key.get()
        if not conference:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        if conference.organizerUserId != user_id:
            raise endpoints.UnauthorizedException('Not the conference organizer')

        # preload necessary data items
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeSessionKey']

        # add default values for those missing (both data model & outbound Message)
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]

        # convert dates from strings to Date objects; set month based on start_date
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], "%H:%M").time()

        if data['typeOfSession']:
            data['typeOfSession'] = str(data['typeOfSession'])
        else:
            del data['typeOfSession']

        # generate Conference Key based on websafeKey and
        # Session key based on Conference key
        # Get Session key from ID
        session_id = Session.allocate_ids(size=1, parent=conference_key)[0]
        session_key = ndb.Key(Session, session_id, parent=conference_key)
        data['key'] = session_key



        session = Session(**data)
        session.put()

        # Task to set new featured speaker if necessary
        taskqueue.add(
            params={
                'speaker': session.speaker,
                'conference_key': request.websafeConferenceKey
            },
            url='/tasks/set_featured_speaker'
        )

        return self._copySessionToForm(session)

    @endpoints.method(SESS_TYPE_REQUEST, SessionForms,
        path='getConferenceSessionsByType',
        http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Gets all sessions in a conference of a certain type"""
        conference_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if conference_key.kind() != 'Conference':
            raise endpoints.BadRequestException(
                'Websafekey %s is not a valid conference key' % request.websafeConferenceKey)

        sessions = Session.query(ancestor=conference_key).filter(Session.typeOfSession == str(request.typeOfSession))

        # return set of SessionForm objects of a certain type
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

# - - - Wishlist - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(SESS_GET_REQUEST, BooleanMessage,
                      path='profile/wishlist/{websafeSessionKey}',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Adds a session to the user's wishlist"""
        return self._sessionWishlist(request)

    @endpoints.method(SESS_GET_REQUEST, BooleanMessage,
                      path='profile/wishlist/{websafeSessionKey}',
                      http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Deletes a session from a user's wishlist"""
        return self._sessionWishlist(request, add=False)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='profile/wishlist',
                      http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Returns the sessions in the user's wishlist"""
        profile = self._getProfileFromUser()
        wishlist = profile.sessionWishlist

        # create list of Session Key objects
        session_keys = [(ndb.Key(urlsafe=session_key)) for session_key in wishlist]
        sessions = ndb.get_multi(session_keys)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    def _sessionWishlist(self, request, add=True):
        """Add or delete a session from a user's wishlist"""
        profile = self._getProfileFromUser()
        session_websafekey = request.websafeSessionKey
        session = ndb.Key(urlsafe=session_websafekey).get()

        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % session_websafekey
            )

        if add:
            if session_websafekey in profile.sessionWishlist:
                raise ConflictException(
                    "You already have this session in your wishlist"
                )
            else:
                profile.sessionWishlist.append(session_websafekey)
                return_value = True
        else:
            if session_websafekey in profile.sessionWishlist:
                profile.sessionWishlist.remove(session_websafekey)
                return_value = True
            else:
                return_value = False

        profile.put()

        return BooleanMessage(data=return_value)

# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(SESS_TIME_REQUEST, SessionForms,
                      path='getConferenceSessionsByTime',
                      http_method='GET', name='getConferenceSessionsByTime')
    def getConferenceSessionsByTime(self, request):
        """Gets all the Sessions for a Conference within a time period"""

        conference_id = request.websafeConferenceKey
        conference_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if conference_key.kind() != 'Conference':
            raise endpoints.BadRequestException(
                'Key %s is not a valid conference key' % conference_id
            )

        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # add this to all time objects to get the right datetime for querying datastore
        filler_date = datetime_date(1970, 1, 1)

        date = datetime.strptime(data['conferenceDate'], "%Y-%m-%d")

        startTime = datetime.strptime(data['startTime'], "%H:%M").time()
        startDateTime = datetime.combine(date=filler_date, time=startTime)

        endTime = datetime.strptime(data['endTime'], "%H:%M").time()
        endDateTime = datetime.combine(date=filler_date, time=endTime)

        dateFilter = ndb.query.FilterNode(
            'date',
            OPERATORS['EQ'],
            date,
        )

        startTimeFilter = ndb.query.FilterNode(
            'startTime',
            OPERATORS['GTEQ'],
            startDateTime,
        )

        endTimeFilter = ndb.query.FilterNode(
            'startTime',
            OPERATORS['LTEQ'],
            endDateTime,
        )

        sessions = Session.query(ancestor=conference_key).filter(dateFilter)\
           .filter(startTimeFilter).filter(endTimeFilter)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='getAttendedConferenceSessions',
                      http_method='GET',
                      name='getAttendedConferenceSessions')
    def getAttendedConferenceSessions(self, request):
        """Gets all the Sessions in the Conferences that a user is attending"""
        conference_keys = self._getConferencesToAttend(request, forms=False)

        # get a list of queries, one for each conference attended
        session_queries = []
        for key in conference_keys:
            session_queries.append(Session.query(ancestor=key))

        # copy all sessions in those queries to a single list of forms
        session_form_list = []
        for session_query in session_queries:
            for session in session_query:
                session_form_list.append(self._copySessionToForm(session))

        return SessionForms(
            items=session_form_list
        )

    @endpoints.method(SessionTypeTimeForm, SessionForms,
                      path='getSessionsExcludeTypeTime',
                      http_method='GET',
                      name='getSessionsExcludeTypeTime')
    def getSessionsExcludeTypeTime(self, request):
        """Gets all Sessions not of the specified type and before the specified time"""

        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # add this to all time objects to get the right datetime for querying datastore
        filler_date = datetime_date(1970, 1, 1)

        # set time filter
        try:
            latestTime = datetime.strptime(data['latestTime'], "%H:%M").time()
            latestDateTime = datetime.combine(date=filler_date, time=latestTime)
            timeFilter = ndb.query.FilterNode('startTime', OPERATORS['LT'], latestDateTime)
        except (KeyError, TypeError):
            timeFilter = None

        # set type filter
        try:
            excludedSession = data['excludedSessionType']
            allowed_types = SessionType.to_dict().keys()
            allowed_types.remove(str(excludedSession))
        except (KeyError, ValueError):
            allowed_types = None

        sessions = Session.query()
        if timeFilter:
            sessions = sessions.filter(timeFilter)

        if allowed_types:
            correct_sessions = []
            for session in sessions:
                    if session.typeOfSession in allowed_types:
                        correct_sessions.append(session)
        else:
            correct_sessions = sessions

        return SessionForms(
            items=[self._copySessionToForm(session) for session in correct_sessions]
        )

# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='getFeaturedSpeaker',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured speaker from memcache"""
        return StringMessage(data=str(memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)) or "")


    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @staticmethod
    def _cacheFeaturedSpeaker(speaker, conference_websafekey):
        """
        Create a featured speaker and assign to memcache
        """
        conference_key = ndb.Key(urlsafe=conference_websafekey)

        # getting sessions from speaker in the same conference
        sessions = Session.query(ancestor=conference_key).filter(Session.speaker == speaker)

        # if speaker has 2 or more sessions at the conference, set new featured speaker
        if sessions.count() >= 2:
            speaker_dict = {'speaker': speaker}

            list_of_session_names = []
            for session in sessions:
                list_of_session_names.append(session.name)
            speaker_dict['sessions'] = list_of_session_names

            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, speaker_dict)
        else:
            data = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)

            # if memcache entry doesn't exist already, set it to an empty string
            if not data:
                memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, '')


api = endpoints.api_server([ConferenceApi]) # register API
