# Running the Application
1. Go to [conferencecentral-1180.appspot.com](https://conferencecentral-1180.appspot.com/).
2. Or, if you want to go to the API explorer, go to [conferencecentral-1180.appspot.com/_ah/api/explorer/](https://conferencecentral-1180.appspot.com/_ah/api/explorer).

**To actually get it set up locally, follow these instructions:**

1. Update the value of application in app.yaml to the app ID you have registered in the App Engine admin console and would like to use to host your instance of this sample.
2. Update the values at the top of settings.py to reflect the respective client IDs you have registered in the Developer Console.
3. Update the value of CLIENT_ID in static/js/app.js to the Web client ID
(Optional) Mark the configuration files as unchanged as follows: $ git update-index --assume-unchanged app.yaml settings.py static/js/app.js
4. Run the app with the devserver using dev_appserver.py DIR, and ensure it's running by visiting your local server's address (by default localhost:8080.)
(Optional) Generate your client library(ies) with the endpoints tool.
5. Deploy your application.

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

Duration is an integer property and is supposed to represent the length of the Session in minutes. So `duration = 120` would mean the Session should take 2 hours. There were no endpoint methods that required duration, but if there were, you could make use of `timedelta` to get estimated end times and such.

typeOfSession should be an enumField, because I felt that there are only so many types of Sessions. They could be either `WORKSHOP`, `LECTURE`,  or `NETWORKING`. There could be more (maybe `PRODUCT_DEMO` or `GOLF`?), but I don't believe there'd be so many more that it should just be its own entity where a user could define as many new SessionTypes as he/she would like. That would be overengineering the solution.

```py
class SessionType(messages.Enum):
    """SessionType -- different kinds of session types"""
    NOT_SPECIFIED = 1
    WORKSHOP = 2
    NETWORKING = 3
    LECTURE = 4
```

Speakers, on the other hand, would make sense as its own entity. For a large conference application, there could be dozens of conferences and hundreds of speakers, so it's natural that speakers should have their own entity. This would allow more flexibility with speakers - perhaps speaker entities could represent possibly multiple individuals (if, say, a company is giving a demonstration, rather than just one person). For this project, I don't think the functionality made this quite necessary, but I know I could implement it with just a little extra bit of work.

The trickiest part of this whole process was understanding just how EnumFields are processed as forms versus how they're stored in actual Session entities in datastore. 

One of the things that I got hung up on was that, for any Form object, if the API request doesn't specify a value for field, then the field's value is None (instead of not being a key at all). Thus, in code like the snippet below, you must make sure to delete fields you know will be null as well as fields that you have specified a default value for in `models.py`. 

```py
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeSessionKey']
```

Otherwise, when you copy the Session you create with `data` to a SessionForm, for stuff like `typeOfSession`, this code will give you trouble because it tries to get the value for a None-type object.

```py
	setattr(session_form, field.name, getattr(SessionType, getattr(session, field.name)))
```

# Queries
I decided the application should be able to get all the Sessions within a certain time period and all the Sessions for all of the Conferences that a user is actually attending. 

`getConferenceSessionsByTime`
The reasoning for the first query is that a user might want to know all the possible Sessions he/she can attend within a certain time range.

`getAttendedConferenceSessions`
The second query might be used by a user who just wants to look at all the queries he/she can possibly attend over all the Conferences that he/she is actually registered for.

# Problematic Query
`getSessionsExcludeTypeTime`
This query requires two filters to be used: one for excluding all Sessions of a certain typeOfSession and another for getting all Sessions whose startTimes occur before a specified time. Since this would require two inequality filters, we could either restructure our models somehow so that it would only require one inequality filter, or we could make two separate queries and combine them.

I decided to use an inequality filter for startTime and then select all the Sessions resulting from that query that aren't of the excluded typeOfSession. Since there aren't so many SessionTypes (it's a discrete variable, versus time which is more or less continuous), I figured I'd just have to iterate through a list of allowed types and select Sessions that are in that list.

In this code, I filter by time and then add Sessions of allowed types into the list of `correct_sessions`.
```py
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
```

I get the `allowed_types` in the following manner:
```py
        try:
            excludedSession = data['excludedSessionType']
            allowed_types = SessionType.to_dict().keys()
            allowed_types.remove(str(excludedSession))
        except (KeyError, ValueError):
            allowed_types = None
```