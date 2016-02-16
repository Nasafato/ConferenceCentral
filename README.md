# Running the Application
1. Go to [conferencecentral-1180.appspot.com](https://conferencecentral-1180.appspot.com/).
2. Or, if you want to go to the API explorer, go to [conferencecentral-1180.appspot.com/_ah/api/explorer/](https://conferencecentral-1180.appspot.com/_ah/api/explorer).
3. Or, use Google App Engine Launcher to add this project's directory and then run it/deploy it. If run locally, you can access the local webpage at [localhost:8080](http://localhost:8080).

# Creating Sessions
Sessions have most of the usual fields. It's pretty apparent right here what they are:

```py
class Session(ndb.Model):
    """Session -- stores a session"""
    name = ndb.StringProperty(required=True)
    highlights = ndb.StringProperty()
    speaker = ndb.StringProperty()
    duration = ndb.IntegerProperty()
    typeOfSession = ndb.StringProperty(default='NOT_SPECIFIED')
    date = ndb.DateProperty()
    startTime = ndb.TimeProperty()
```

Sessions are children of Conferences. The trickiest part of implementing Session and Session Forms was getting `typeOfSession` and `startTime` and `date` to work correctly.

Basically, the problems came from converting EnumField objects into strings and vice-versa so that the strings could be put into Session objects while the EnumFields could be copied into SessionForms.

```py
class SessionType(messages.Enum):
    """SessionType -- different kinds of session types"""
    NOT_SPECIFIED = 1
    WORKSHOP = 2
    NETWORKING = 3
    LECTURE = 4
```

I figured for stuff like speakers, I could just use a StringProperty. 

# Queries
I decided the application should be able to get all the Sessions within a certain time period and all the Sessions for all of the Conferences that a user is actually attending. 

The reasoning for the first query is that a user might want to know all the possible Sessions he/she can attend within a certain time range.

The second query might be used by a user who just wants to look at all the queries he/she can possibly attend over all the Conferences that he/she is actually registered for.

# Problematic Query
This query requires two filters to be used: one for excluding all Sessions of a certain typeOfSession and another for getting all Sessions whose startTimes occur before a specified time.

```py
    @endpoints.method(SessionTypeTimeForm, SessionForms,
                      path='getSessionsExcludeTypeTime',
                      http_method='POST',
                      name='getSessionsExcludeTypeTime')
    def getSessionsExcludeTypeTime(self, request):
        """Gets all Sessions not of the specified type and before the specified time"""

        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # add this to all time objects to get the right datetime for querying datastore
        filler_date = datetime_date(1970, 1, 1)

        # set time filter
        try:
            startTime = datetime.strptime(data['startTime'], "%H:%M").time()
            startDateTime = datetime.combine(date=filler_date, time=startTime)
            timeFilter = ndb.query.FilterNode('startTime', OPERATORS['LT'], startDateTime)
        except KeyError:
            timeFilter = None

        # set type filter
        try:
            excludedSession = data['excludedSessionType']
            typeFilter = ndb.query.FilterNode('typeOfSession', OPERATORS['NE'], str(excludedSession))
        except KeyError:
            typeFilter = None

        sessions = Session.query()

        if timeFilter:
            sessions = sessions.filter(timeFilter)
        if typeFilter:
            sessions = sessions.filter(typeFilter)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )
```
