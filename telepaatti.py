#!/usr/bin/env python
"""

Telepaatti, IRC to Jabber/XMPP gateway.

Copyright (C) 2007-2009 Petteri Klemola

Telepaatti is free software; you can redistribute it and/or modify it
under the terms of the GNU General Public License version 3 as
published by the Free Software Foundation.

Telepaatti is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301, USA.

For more information about Telepaatti see http://23.fi/telepaatti

"""

import socket
import time, datetime
import exceptions
from threading import *
from xmpp import *
import getopt, sys

STATUSSTATES = ['AVAILABLE','CHAT', 'AWAY', 'XA', 'DND', 'INVISIBLE']
TELEPAATTIVERSION = 1

class JabberThread(Thread):
    """Class for Jabber connection thread"""

    def __init__(self, client):
        """Constructor for JabberThread Class

        @type client: Client
        @param client: instace of xmpp.Client class
        """
        Thread.__init__(self)
        self.client = client
        self.connected = True

    def process(self):
        """Starts the xmpp client process"""
        try:
            self.client.Process(1)
        except:
            return False
        return True

    def run(self):
        """When xmpp client is connected runs the client process """
        time.sleep(5)
        while self.client.Process(1) and self.connected:
            pass
        self.client.disconnect()
        self.connected = False

class ClientThread(Thread):
    """ ClientThread class for handling IRC and Jabber connections."""
    def __init__(self,socket, port, JabberID, passwd, debug=False):
        """Constructor for ClientThread class

        @type socket: socket
        @type port: integer
        @type JabberID: JID
        @type passwd: string
        @param socket: socket on which the connection is made
        @param port: port of the connection
        @param JabberID: the Jabber ID of the connetor
        @param passwd: the passwd of the Jabber ID
        """
        Thread.__init__(self)
        self.socket = socket
        self.port = port
        self.passwd = passwd
        self.JID = JID("%s/%s" %(JabberID,'telepaatti'))
        client=Client(self.JID.getDomain(),debug=[])

        client.connect(proxy={})
        client.RegisterHandler('message',self.messageHandler)
        client.RegisterHandler('presence',self.presenceHandler)
        client.RegisterHandler('iq' ,self.iqHandler)

        client.auth(self.JID.getNode(), self.passwd, self.JID.getResource())

        client.sendInitPresence()

        self.client = client
        self.nickname = JabberID[:JabberID.find('@')]
        self.newnick = ''

        self.notFirstNick = False
        self.mucs = {}
        self.mucs['roster'] = {}

        self.rosterMessages = list()
        self.joinedRoster = False

        self.connected = True

        self.nickmapper = {}

        self.nickChangeInMucs = {}

        self.joinQueue = {}
        self.roomPingQueue = {}
        self.disconnectedMucs = {}
        self.pingCounter = 0

        self.xmppSem = BoundedSemaphore(value=1)

        # if set to True debug messages are printed to sdt out
        self.debug = debug

    def printError(self, msg):
        """Error message printing for std out

        @type msg: string
        @param msg: error message
        """
        dt = "%s" % datetime.datetime.now()
        dt = dt[:-7]
        print "ERROR: [%s] %s" % (dt, msg)

    def printDebug(self, msg):
        """print Debug message to std out

        @type msg: string
        @param msg: debug message
        """
        if not self.debug:
            return
        dt = "%s" % datetime.datetime.now()
        dt = dt[:-7]
        print "DEBUG [%s] %s" % (dt, msg)

    def getMucs(self):
        """Return joined MUC without roster MUC

        @rtype: list
        @return: list of mucs joined (without roster)
        """
        mucs = self.mucs.keys()
        mucs.remove('roster')
        return mucs

    def fixNick(self, nick):
        """Fixes strange character nicknames that don't work nicely with
        IRC. This function may cause conflicts and thus unfinished.

        @type nick: string
        @param nick: nickname to fix
        @rtype: string
        @return: fixed nick
        """
        nick = unicode(nick)
        fixednick = nick.replace(' ', '_')
        fixednick = fixednick.replace('!', '_')
        fixednick = fixednick.replace(':', '_')
        self.nickmapper[fixednick] = nick
        return fixednick

    def unfixNick(self, fixednick):
        """Reverses nicks fixed with fixNick function. Is also unfinished

        @type fixednick: string
        @param fixednick: nickname to unfix
        @rtype: string
        @return: unfixed nick
        """
        nick = fixednick
        try:
            nick = self.nickmapper[fixednick]
        except:
            self.printError('unfixNick did not work')
        return nick

    def makeIRCACTION(self, msg):
        """Makes IRC action message

        @type msg: string
        @param msg: message from which to make IRC action
        @rtype: string
        @return: IRC action message
        """
        msg = '\001ACTION %s\001' % msg
        return msg

    def sendToIRC(self, msg):
        """Sends message IRC client

        @type msg: string
        @param msg: message to send
        """
        msg = msg.encode('utf-8')
        msg = "%s\r\n" % msg
        self.printDebug(msg)
        try:
            self.socket.send(msg)
        except:
            self.connected = False
            self.printError('FATAL ERROR WHILE TRYING TO WRITE IRC MESSAGE TO SOCKET, DISCONNECTING')

    def sendToXMPP(self, msg):
        """Sends message XMPP server

        @type msg: string
        @param msg: message to send
        """
        self.xmppSem.acquire()
        self.client.send(msg)
        self.xmppSem.release()

    def ircGetStatus(self, nick, channel):
        """Get IRC status

        @type nick: string
        @type channel: string
        @param nick: nick whos status to get
        @param channel: the channel on which status to get
        @rtype: string
        @return: IRC-style status string
        """
        sta = 'H'
        show = ''
        role = ''
        if self.mucs.has_key(channel):
            show = self.mucs[channel][nick]['show']
            role = self.mucs[channel][nick]['role']
        if show in ['away','xa', 'dnd']:
            sta = 'G'
        if role == 'moderator':
            sta = '%s@' % sta
        elif role == 'participant':
            sta = '%s+' % sta
        return sta

    def ircCommandJOIN(self, nick, channel):
        """IRC command join channel

        @type nick: string
        @type channel: string
        @param nick: nick who is joining
        @param channel: channel whats been joined
        """
        onick = nick
        resource = self.fixNick(nick.getResource())
        nick = self.fixNick(nick)
        msg = ':%s!%s JOIN :#%s' % (
            resource,
            nick,
            channel)
        self.sendToIRC(msg)

        role = self.mucs[channel][onick]['role']
        args = ''
        if role == 'moderator':
            args = '+o'
        elif role == 'participant':
            args = '+v'
        if args:
            self.ircCommandMODEMUCUSER(JID('%s/telepaatti' % channel), onick, channel, args)

    def ircCommandSELFJOIN(self, nick, room):
        """IRC command join channel

        @type nick: string
        @type room: string
        @param nick: nick who is joining
        @param room: channel whats been joined
        """
        snick = self.fixNick(self.nickname)
        lines = list()
        lines.append(':%s JOIN :#%s'% (snick, room))
        lines.append(':localhost MODE #%s' % room)

        for nick in self.mucs[room].iterkeys():
            onick = nick
            nick = nick.getResource()
            nick = self.fixNick(nick)
            oonick = nick
            if self.mucs[room][onick]['role'] == 'moderator':
                oonick = "@%s" % oonick
            elif self.mucs[room][onick]['role'] == 'participant':
                oonick = "+%s" % oonick
            lines.append(':localhost 353 %s = #%s :%s' % (snick, room, oonick))
        lines.append(':localhost 366 %s #%s :End of /NAMES list.'% (snick, room))
        while lines:
            msg = lines.pop(0)
            self.sendToIRC(msg)

    def ircCommandPART(self, nick, channel, text):
        """IRC command part channel

        @type nick: string
        @type channel: string
        @type text: string
        @param nick: nick who is parting
        @param channel: channel whats been parted
        @param text: the part message
        """
        resource = self.fixNick(nick.getResource())
        nick = self.fixNick(nick)
        msg = ':%s!%s PART #%s :%s' % (
            resource,
            nick,
            channel,
            text)
        self.sendToIRC(msg)

    def ircCommandROSTERPART(self):
        """Part roster channel"""
        self.joinedRoster = False
        msg = ':%s!%s PART #roster : ' % (self.nickname,
                                              unicode(self.JID))
        self.sendToIRC(msg)

    def ircCommandROSTERPRIVMSGMUC(self, msg):
        """Handles messages in roster channel. For example if user types HELP
        in roster channel (s)he gets list of available commands

        @type msg: string
        @param msg: message to roster channel
        """
        if msg.upper().startswith('HELP'):
            msg = ':%s PRIVMSG #%s :%s' % \
                ('Telepaatti',
                 'roster',
                 'Awailable commands !subscribe, !unsubscribe, !subscribed, !unsubscribed')
            self.sendToIRC(msg)
        elif msg.startswith('!'):
            msglist = msg[1:].split()
            msglist[0] = msglist[0].upper()
            if len(msglist) == 2:
                if msglist[0] == 'SUBSCRIBE':
                    self.xmppCommandSUBSCRIBE(msglist[1])
                elif msglist[0] == 'UNSUBSCRIBE':
                    self.xmppCommandUNSUBSCRIBE(msglist[1])
                elif msglist[0] == 'SUBSCRIBED':
                    self.xmppCommandSUBSCRIBED(msglist[1])
                elif msglist[0] == 'UNSUBSCRIBED':
                    self.xmppCommandUNSUBSCRIBED(msglist[1])
        return

    def ircCommandROSTERSELFJOIN(self):
        """Join roster channel. This handles cases when user self joins to
        roster channel"""
        snick = self.fixNick(self.nickname)
        lines = list()
        room = 'roster'
        lines.append(':%s JOIN :#%s'% (snick, room))
        lines.append(':localhost 332 %s #%s :Roster Channel. Telepaatti is tracking here your roster changes. Type Help for help!' %
                     (snick, room))
        lines.append(':localhost 333 %s #%s telepaatti 000000001' % (snick, room))
        lines.append(':localhost MODE #%s' % room)
        lines.append(':localhost 353 %s = #%s :%s' % (snick, room, snick))

        for nick in self.mucs[room].iterkeys():
            nick = self.fixNick(nick)
            if not (snick == nick):
                lines.append(':localhost 353 %s = #%s :%s' % (snick, room, nick))
        lines.append(':localhost 366 %s #%s :End of /NAMES list.'% (snick, room))

        while lines:
            msg = lines.pop(0)
            self.sendToIRC(msg)

        self.joinedRoster = True
        for msg in self.rosterMessages:
            self.sendToIRC(msg)

    def ircCommandROSTERMSG(self, pres):
        """Roster channel messages. Here is traced roster users statas

        @type pres: Presence
        @param pres: Presence of the roster user
        """
        nick = pres.getFrom()
        jid = pres.getFrom()
        message = ''
        dt = "%s" % datetime.datetime.now()
        dt = dt[:-7]
        if jid != str(self.JID) and pres.getType() == 'unavailable': # left
            message = ':%s!%s PART #roster :[%s] Offline' % (nick,jid,dt)
            try: # remove
                del (self.mucs['roster'][jid])
            except:
                self.printError('GOT UNEXPECTED ERROR')
        elif jid != str(self.JID) and not self.mucs['roster'].has_key(jid):
            message = ':%s!%s JOIN :#roster' % (nick, jid)
            self.mucs['roster'][jid] = { 'role': 'participant',
                                         'affiliation': 'member'}
        else:
            action = self.makeIRCACTION('[%s] %s - %s' % (
                    dt,
                    pres.getShow(),
                    pres.getStatus()))
            message = ':%s!%s PRIVMSG #roster :%s' % (
                nick,
                jid,
                action)
        self.rosterMessages.append(message)
        if self.joinedRoster:
            self.sendToIRC(message)

    def ircCommandPRIVMSG(self, nick, text, timestamp=''):
        """Converts private messages to IRC client

        @type nick: string
        @type text: string
        @type timestamp: string
        @param nick: the nickname whois messaging
        @param text: the message
        @param timestamp: timestamp of the message
        """
        nick = self.fixNick(nick)
        nick = unicode(nick)
        lines = text.splitlines()
        messages = list()
        for line in lines:
            action = False
            if line.upper().startswith('/ME '):
                line = line[4:]
                action = True
            if timestamp:
                line = "[%s] %s " % (timestamp, line)
            if action:
                line = self.makeIRCACTION(line)

            msg = ':%s!%s PRIVMSG %s :%s' % (nick, nick, self.fixNick(self.nickname),line)
            messages.append(msg)
        for msg in messages:
            self.sendToIRC(msg)

    def ircCommandPRIVMSGMUC(self, orgnick, channel, text, timestamp=''):
        """Converts MUC messages to IRC channel messages

        @type orgnick: string
        @type channel: string
        @type text: string
        @type timestamp: string
        @param orgnick: the nickname whois messaging
        @param channel: the channel
        @param text: the message
        @param timestamp: timestamp of the message
        """
        lines = text.splitlines()
        messages = list()
        for line in lines:
            action = False
            if line.upper().startswith('/ME '):
                line = line[4:]
                action = True
            if timestamp:
                line = "[%s] %s " % (timestamp, line)
            if action:
                line = self.makeIRCACTION(line)
            nick = orgnick.getResource()
            nick = self.fixNick(nick)
            msg = ':%s PRIVMSG #%s :%s' % (nick,
                                               channel,
                                               line)
            messages.append(msg)
        for msg in messages:
            self.sendToIRC(msg)

    def ircCommandTOPIC(self, nick, channel, text):
        """Converts MUC topic to IRC channel topic

        @type nick: string
        @type channel: string
        @type text: string
        @param channel: name of the channel
        @param nick: the nickname whois messaging
        @param text: the message
        """
        snick = self.fixNick(nick.getResource())
        nick = self.fixNick(nick)
        msg =':%s!%s TOPIC #%s :%s' % (snick, nick, channel, text)
        self.sendToIRC(msg)

    def ircCommandMODEMUC(self, channel, args):
        """Converts MUC mode to IRC channel mode

        @type channel: string
        @type args: string
        @param channel: anme of the channel
        @param args: arguments of the mode
        """
        nick = self.fixNick(self.nickname)
        msg = ':localhost 324 %s #%s %s' % (nick, channel, args)
        self.sendToIRC(msg)
        msg = ':localhost 329 %s #%s %s' % (nick, channel, '1031538353')
        self.sendToIRC(msg)

    def ircCommandMODEMUCBANLIST(self, channel):
        """Converts MUC ban list to IRC channel banlist, is unfinished

        @type channel: string
        @param channel: name of the channel
        """
        nick = self.fixNick(self.nickname)
        msg = ':localhost 368 %s #%s :End of Channel Ban List' % (nick, channel)
        self.sendToIRC(msg)

    def ircCommandMODEMUCUSER(self, giver, taker, muc, args):
        """Convers MUC user mode to IRC channel user mode. Example use cases
        are when someone is granted a voice or admin rights on a MUC.

        @type giver: string
        @type taker: string
        @type muc: string
        @type args: string
        @param giver: giver of the new mode
        @param taker: taker of the new mode
        @param muc: MUC on which the mode takes place
        @param args: arguments of the mode
        """
        givernick = self.fixNick(giver.getResource())
        giver = self.fixNick(giver)
        taker = self.fixNick(taker.getResource())
        msg = ':%s!%s MODE #%s %s %s' % (givernick,
                                         giver,
                                         muc,
                                         args,
                                         taker)
        self.sendToIRC(msg)

    def ircCommandMODE(self, args):
        """Converts XMPP mode to IRC mode. Unfinished

        @type args: string
        @param args: arguments of the mode
        """
        # just to keep irssi happy, fix later to more meaningfull
        nick = self.fixNick(self.nickname)
        msg = ':%s MODE %s :%s' % (nick, nick, args)
        self.sendToIRC(msg)

    def ircCommandERROR(self, message='', ircerror=0):
        """Convert XMPP errors to IRC errors

        @type message: string
        @type ircerror: integer
        @param message: the error message
        @param ircerror: number of the error
        """
        msg = ''
        if ircerror == 0:
            msg = 'ERROR :XMPP ERROR %s' % message
        elif ircerror == -1:
            msg = 'ERROR :Telepaatti error %s' % message
        elif ircerror == 403:
            msg = ':localhost 403 %s localhost :That channel doesn\'t exist' % self.fixNick(self.nickname)
        self.sendToIRC(msg)

    def ircCommandERRORMUC(self, number, errormess, room):
        """Convert XMPP MUC errors to IRC channel errors

        @type number: integer
        @type errormess: string
        @type room: string
        @param number: number of the error
        @param errormess: the error message
        @param room: the MUC
        """
        text = ''
        if number == 403:
            text = 'No such channel'
        elif number == 404:
            text = 'Cannot send to channel'
        elif number == 467:
            text = 'Channel key already set'
        elif number == 471:
            text = 'Cannot join channel (+l)'
        elif number == 473:
            text = 'Cannot join channel (+i)'
        elif number == 474:
            text = 'Cannot join channel (+b)'
        elif number == 475:
            text = 'Cannot join channel (+k)'
        elif number == 476:
            text = 'Bad Channel Mask'
        elif number == 477:
            text = 'Channel doesn\'t support modes'
        elif number == 478:
            text = 'Channel list is full'
        elif number == 481:
            text = 'Permission Denied- You\'re not an IRC operator'
        elif number == 482:
            text = 'You\'re not channel operator'
        else:
            text = 'No text'

        msg = ':localhost %s %s #%s :%s' % (
            number, self.fixNick(self.nickname), room, text)
        self.sendToIRC(msg)
        self.ircCommandERROR(errormess)

    def ircCommandWHO(self, users, channel):
        """Convert XMPP vcard query to IRC who

        @type users: list
        @type channel: string
        @param users: list of users
        @param channel: the channel (MUC)
        """
        for user in users:
            oruser = user
            user = unicode(user)
            at = user.find('@')
            slash = user.find('/')
            domain = channel[at+1:]
            if at > 0 and slash > at:
                onick = user[slash+1:]
            user = self.fixNick(user)
            nick = self.fixNick(onick)
            msg = ':localhost 352 %s #%s %s %s localhost %s %s :0 %s' % (
                self.fixNick(self.nickname),
                channel,
                self.fixNick(user[:at]),
                self.fixNick(user[at+1:]),
                nick,
                self.ircGetStatus(oruser, channel),
                onick)
            self.sendToIRC(msg)

        msg = ':localhost 315 %s #%s :End of /WHO list.' % (self.fixNick(self.nickname), channel)
        self.sendToIRC(msg)

    def ircCommandWHOIS(self, jid):
        """Convert XMPP vcard query to IRC whois

        @type jid: JID
        @param jid: Jabber id of the user whos vcard is quered
        """
        nick = self.fixNick(self.nickname)
        whonick = jid
        if self.mucs.has_key(jid.getStripped()):
            whonick = self.fixNick(jid.getResource())
        lines = [
            ':localhost 311 %s %s %s %s * : %s' % (nick,
                                                   whonick,
                                                   jid.getNode(),
                                                   jid.getDomain(),
                                                   whonick),
            ':localhost 312 %s %s localhost : XMPP telepaatti' % (nick, whonick),
            ':localhost 318 %s %s :End of /WHOIS list.' % (nick, whonick)]
        while lines:
            self.sendToIRC(lines.pop(0))

    def ircCommandUNAWAY(self):
        """Convert XMPP status to IRC away"""
        nick = self.fixNick(self.nickname)
        msg = ':localhost 305 %s :%s' % (
            nick,
            'You are no longer marked as being away')
        self.sendToIRC(msg)

    def ircCommandNOWAWAY(self):
        """Convert XMPP status to IRC not away"""
        nick = self.fixNick(self.nickname)
        msg = ':localhost 306 %s :%s' % (
            nick,
            'You have been marked as being away')
        self.sendToIRC(msg)

    def ircCommandNOTICE(self, text):
        """Convert XMPP message to IRC notice

        @type text: string
        @param text: notice text
        """
        nick = self.fixNick(self.nickname)
        msg = 'NOTICE %s :%s' % (nick, text)
        self.sendToIRC(msg)

    def ircCommandSUBSCRIBE(self, pres):
        """Convert XMPP subscribe message to IRC message

        @type pres: presence
        @param pres: presence of which to subscribe
        """
        text = "is making subsciption request to you with message: \"%s\" You MUST either approve the request or refuse the request. You can approve it by joinin #roster channel andthen type \"!subscribed %s\" if you wish to subscibe to contact or \"!unsubscribed %s\" if you wish not to subscibe to contact" % (pres.getStatus(), pres.getFrom(), pres.getFrom())
        text = self.makeIRCACTION(text)
        self.printDebug(str(pres))
        self.ircCommandPRIVMSG(pres.getFrom(), text)

    def ircCommandUNSUBSCRIBE(self, pres):
        """Convert XMPP unsubscribe message to IRC message. Unfinished.

        @type pres: presence
        @param pres: presence of which to unsubscribe
        """
        self.printDebug(str(pres))

    def ircCommandSUBSCRIBED(self, pres):
        """Convert XMPP subscribed message to IRC message. Unfinished.

        @type pres: presence
        @param pres: presence of which to subscribed
        """
        self.printDebug(str(pres))

    def ircCommandUNSUBSCRIBED(self, pres):
        """Convert XMPP unsubscribed message to IRC message. Unfinished.

        @type pres: presence
        @param pres: presence of which to unsubscribed
        """
        self.printDebug(str(pres))

    def xmppCommandSUBSCRIBE(self, jid):
        """Send XMPP subscribe message.

        @type jid: string
        @param jid: Jabber ID to subscribe
        """
        self.sendToXMPP(Presence(to='%s' % jid,
                                 typ = 'subscribe'))

    def xmppCommandUNSUBSCRIBE(self, jid):
        """Send XMPP unsubscribe message.

        @type jid: string
        @param jid: Jabber ID to unsubscribe
        """
        self.sendToXMPP(Presence(to='%s' % jid,
                                 typ = 'unsubscribe'))

    def xmppCommandSUBSCRIBED(self, jid):
        """Send XMPP subscribed message.

        @type jid: string
        @param jid: Jabber ID to subscribed
        """
        self.sendToXMPP(Presence(to='%s' % jid,
                                 typ = 'subscribed'))

    def xmppCommandUNSUBSCRIBED(self, jid):
        """Send XMPP unsubscribed message.

        @type jid: string
        @param jid: Jabber ID to unsubscribed
        """
        self.sendToXMPP(Presence(to='%s' % jid,
                                 typ = 'unsubscribed'))

    def xmppCommandMUCMODE(self, jid):
        """Send XMPP MUC mode change

        @type jid: string
        @param jid: Jabber id of the MUC
        """
        if jid == 'roster': # no query for roster
            return
        iq = protocol.Iq(to=jid,
                         queryNS=NS_DISCO_INFO,
                         typ = 'get')
        iq.setID('disco3')
        self.sendToXMPP(iq)

    def xmppCommandMUCUSERS(self, jid):
        """Send XMPP MUC users query

        @type jid: string
        @param jid: Jabber id of the MUC
        """
        if jid == 'roster': # no query for roster
            return
        iq = protocol.Iq(frm=unicode(self.JID),
                         to=jid,
                         queryNS=NS_DISCO_ITEMS,
                         typ = 'get')
        iq.setID('disco4')
        self.sendToXMPP(iq)

    def xmppCommandSTATUS(self, show, status):
        """Send XMPP status change

        @type show: string
        @type status: string
        @param show: status
        @param status: status
        """
        for muc in self.mucs.keys():
            if muc != 'roster':
                p=Presence(to='%s/%s' % (
                        muc,
                        self.nickname))
                if not show == '':
                    p.setShow(show)
                p.setStatus(status)
                self.sendToXMPP(p)
        p=Presence()
        p.setShow(show)
        p.setStatus(status)
        self.sendToXMPP(p)
        if show.upper() in STATUSSTATES[2:]: # available, chat
            self.ircCommandNOWAWAY()
        else:
            self.ircCommandUNAWAY()

    def xmppCommandMUCPRESENCE(self, muc, nick):
        """Send XMPP presence to MUC room

        @type muc: string
        @type nick: string
        @param muc: Jabber ID of the MUC
        @param nick: users nickname for the MUC
        """
        self.sendToXMPP(Presence(to='%s/%s' %
                                 (muc,
                                  nick)))

    def xmppCommandMUCROLE(self, muc, nick, role):
        """Send XMPP MUC role to MUC room

        @type muc: string
        @type nick: string
        @type role: role
        @param muc: Jabber ID of the MUC
        @param nick: users nickname in the MUC
        @param role: role of the user
        """
        iq = protocol.Iq(to=muc,
                         queryNS=NS_MUC_ADMIN,
                         typ = 'set')
        item = iq.getTag('query').setTag('item')
        item.setAttr('nick', nick)
        item.setAttr('role', role)
        self.sendToXMPP(iq)

    def xmppCommandGETWHOIS(self, jid):
        """Send XMPP vcard, last activity and sofware version request for some
        Jabber ID.

        @type jid: string
        @param jid: Jabber ID
        """
        # vcard
        iq = protocol.Iq(to=jid,
                         typ = 'get')
        iq.setTag(NS_VCARD + ' vCard')
        iq.setID('v3')
        self.sendToXMPP(iq)

        # last activity
        iq = protocol.Iq(to=jid,
                         typ = 'get',
                         queryNS=NS_LAST)
        self.sendToXMPP(iq)

        # software version
        if not jid.getResource():
            # try to find match in roster
            rosternicks = self.mucs['roster'].keys()
            for x in rosternicks:
                if x.getStripped() == jid.getStripped():
                    jid = x
            if not  jid.getResource():
                return
        iq = protocol.Iq(to=jid,
                         typ = 'get',
                         queryNS=NS_VERSION)
        self.sendToXMPP(iq)

    def xmppCommandSOFTWAREVERSION(self, jid):
        """Send set software version XMPP. Not finished """
        pass

    def xmppCommandLASTACTIVITY(self, jid):
        """Send last activity XMPP. Not finished """
        pass

    def run(self):
        """Here is this threads main functionality. Jabber-thread is started
        and polling of socket for IRC-messages is in here."""
        jt = JabberThread(self.client)
        jt.start()

        nick = self.fixNick(self.nickname)
        lines = ["NOTICE AUTH :*** Looking up your hostname...",
                 "NOTICE AUTH :*** Found your hostname, welcome back",
                 "NOTICE AUTH :*** Checking ident",
                 "NOTICE AUTH :*** No identd (auth) response",
                 ":localhost 001 %s :Welcome to Telepaatti, IRC to XMPP gateway" %
                 nick,
                 ":localhost 002 %s :Your host is localhost [localhost port %s] running version telepaatti-%s" % (
                nick,
                self.port,
                TELEPAATTIVERSION)
                 ]

        while self.connected and jt.connected:
            try:
                data = self.socket.recv(4096)
            except:
                self.printError('GOT ERROR SHUTTING DOWN')
                self.connected = False
            if data.find ( 'PING' ) != -1:
                # check that our rooms are still alive
                if self.pingCounter == 5:
                    self.pingCounter = 0
                    for muc in self.mucs.keys():
                        if muc != 'roster':
                            if self.disconnectedMucs.has_key(muc):
                                if self.disconnectedMucs[muc] < 5:
                                    self.disconnectedMucs[muc] = self.disconnectedMucs[muc] + 1
                                else:
                                    self.disconnectedMucs[muc] = 0
                                    self.roomPingQueue[muc] = ''
                                    self.xmppCommandMUCMODE(muc)
                            else:
                                self.roomPingQueue[muc] = ''
                                self.xmppCommandMUCMODE(muc)
                else:
                    self.pingCounter = self.pingCounter + 1
                self.sendToIRC('PONG localhost')
            while lines:
                self.sendToIRC(lines.pop(0))
            if data:
                for line in data.splitlines():
                    self.commandHandler(line)
            else:
                self.connected = False
        if not jt.connected:
            self.ircCommandNOTICE('XMPP server disconnected, shutting down Telepaatti.')
        jt.connected = False
        self.socket.shutdown(socket.SHUT_RDWR)

    def messageHandlerError(self, sess, mess):
        """Handle incoming error messages from XMPP

        @type sess: string
        @type mess: Message
        @param sess: session
        @param mess: error message

        """
        text = ''
        try:
            text = mess.getTag('error').getTag('text').getData()
        except:
            pass
        erc = mess.getErrorCode()
        jidFrom = mess.getFrom()
        to = mess.getTo()
        if erc == '403':
            self.ircCommandERRORMUC(482, text, jidFrom)
        else:
            self.printDebug('MUC ERROR NOT IMPLEMENTED')

    def messageHandler(self, sess, mess):
        """Handle incoming XMPP with type message

        @type sess: string
        @type mess: Message
        @param sess: session
        @param mess: XMPP Message
        """
        if mess.getType() == 'error':
            self.messageHandlerError(sess,mess)
            return
        nick = mess.getFrom()
        text = mess.getBody()
        topic = mess.getSubject()
        room = unicode(mess.getFrom())
        x = room.find('/')
        if x > 0:
            room = room[:room.find('/')]
        ts = ''
        if mess.getTag('x',namespace=NS_DELAY):
            ts=mess.getTimestamp()
            if not ts:
                ts=mess.setTimestamp()
                ts=mess.getTimestamp()
            ts = time.strptime(ts,'%Y%m%dT%H:%M:%S')
            ts = datetime.datetime(*ts[:-3])
        if not text and not topic:
            return

        MUC = False
        if mess.getType() == 'groupchat':
            MUC = True

        if not MUC:
            self.ircCommandPRIVMSG(nick, text, ts)
        elif topic:
            self.ircCommandTOPIC(nick, room, topic)
        elif not nick.getResource() == self.nickname or ts:
            self.ircCommandPRIVMSGMUC(nick, room, text, ts)


    def iqHandler(self, con, iq):
        """Handle incoming XMPP with type Iq

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ns = iq.getQueryNS()
        if ns is None:
            ns = iq.getProperties()[0]

        if ns == NS_DISCO and iq.getType() in ['get', 'error']:
            self.iqHandlerInfo(con, iq)
        elif ns == NS_DISCO_ITEMS and iq.getType() == 'result':
            self.iqHandlerItems(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'result':
            self.iqHandlerInfo(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'get':
            self.xmppCommandINFOGET(iq.getFrom())
        elif ns == NS_DISCO_ITEMS and iq.getType() == 'error':
            self.iqHandlerError(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'error':
            self.iqHandlerError(con, iq)
        elif ns == NS_VCARD and iq.getType() == 'result':
            self.iqHandlerVcard(con, iq)
        elif ns == NS_VCARD and iq.getType() == 'error':
            self.iqHandlerVcardError(con, iq)
        elif ns == NS_LAST and iq.getType() == 'result':
            self.iqHandlerLast(con, iq)
        elif ns == NS_LAST and iq.getType() == 'get':
            self.xmppCommandLASTACTIVITY(iq.getFrom())
        elif ns == NS_LAST and iq.getType() == 'error':
            self.iqHandlerLastError(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'result':
            self.iqHandlerVersion(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'error':
            self.iqHandlerVersionError(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'get':
            self.xmppCommandSOFTWAREVERSION(iq.getFrom())
        else:
            self.printDebug('IQ HANDLER FOR THIS NAMESPACE NOT IMPLEMENTED YET')


    def iqHandlerLastError(self,con, iq):
        """Handle incoming XMPP with type Iq and last activity error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerLastError')

    def iqHandlerLast(self, con, iq):
        """Handle incoming XMPP with type Iq and last activity

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ch = iq.getTag('query')
        seconds = ch.getAttr('seconds')
        self.ircCommandNOTICE('** Last active information for %s **' % iq.getFrom())
        self.ircCommandNOTICE('Idle %s second**' % seconds)

    def iqHandlerVersionError(self,con, iq):
        """Handle incoming XMPP with type Iq and version error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerVersionError')

    def iqHandlerVersion(self, con, iq):
        """Handle incoming XMPP with type Iq and version

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ch = iq.getTag('query').getChildren()
        self.ircCommandNOTICE('** Software version information for %s **' % iq.getFrom())
        for c in ch:
            self.ircCommandNOTICE('%s: %s' % (c.getName(), c.getData()))

    def iqHandlerVcardError(self,con, iq):
        """Handle incoming XMPP with type Iq and vcard error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerVcardError')

    def iqHandlerVcard(self, con, iq):
        """Handle incoming XMPP with type Iq and vcard

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        card = {}
        ch = iq.getTag('vCard').getChildren()
        for c in ch:
            name = c.getName()
            if name != 'PHOTO':
                if name == 'EMAIL':
                    emailv = c.getChildren()
                    card['EMAIL %s' % emailv[0].getName()] = emailv[1].getData()
                card[c.getName()] = c.getData()

        self.ircCommandNOTICE('** Vcard information for %s **' % iq.getFrom())
        for key in card.keys():
            for line in card[key].splitlines():
                self.ircCommandNOTICE('%s: %s' % (key, line))

    def iqHandlerError(self, con , iq):
        """Handle incoming XMPP with type Iq and error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        jid = iq.getFrom()
        # this can mess things up, fix later
        if self.roomPingQueue.has_key(jid):
            del (self.roomPingQueue[jid])
        # room errors
        errornum = iq.getErrorCode()
        if jid in self.mucs.keys():
            if errornum == '404' and jid not in self.disconnectedMucs.keys():
                self.ircCommandERRORMUC(404, 'MUC DISCONNECTED', jid)
                self.ircCommandPRIVMSGMUC(JID("%s/%s" % (jid, 'telepaatti')),
                                          jid,
                                          'MUC IS DISCONNECTED YOUR TEXT WILL NOT SHOW ON CHANNEL. YOU CAN WAIT UNTIL MUC CONNECTS AGAIN OR USE /PART TO LEAVE THIS MUC!',
                                          timestamp='')
                self.disconnectedMucs[jid] = 0
                return
            else:
                return
        else:
            self.ircCommandERROR('iq error num %s jid not room! jid %s' % (errornum, jid))


    def iqHandlerItems(self, con, iq):
        """Handle incoming XMPP with type Iq and items

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """

        jid = iq.getFrom()
        if iq.getType() == 'error':
            # some fixing is needed
            self.ircCommandERROR('%s %s ' % (iq.getErrorCode(), iq.getErrorCode()))
        elif iq.getID() == 'disco4': # muc users
            ch = iq.getQueryChildren()
            mucusers = list()
            for c in ch:
                name = c.getName()
                if name == 'item':
                    mucusers.append(c.getAttrs()['jid'])
            if self.mucs.has_key(jid):
                pass # we keep track of users else where
            self.ircCommandWHO(mucusers, unicode(jid))
            return
        else:
            self.printDebug('UNKNOWN DISCO ITEM %s ' % jid)

    def iqHandlerInfo(self, con, iq):
        """Handle incoming XMPP with type Iq and info

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        roomname = iq.getFrom()
        # this can mess things up, fix
        if self.roomPingQueue.has_key(roomname):
            del (self.roomPingQueue[roomname])
            return

        MUC = False
        roomfeats = list()
        if iq.getType() == 'error':
            # fix this later
            self.ircCommandERROR('%s %s ' % (iq.getErrorCode(), iq.getErrorCode()))
        else:
            ch = iq.getQueryChildren()
            for c in ch:
                name = c.getName()
                if name == 'identity':
                    atrs = c.getAttrs()
                    if atrs.has_key('type') and atrs.has_key('category'):
                        if atrs['type'] == 'text' and \
                                atrs['category'] == 'conference':
                            MUC = True
                elif name == 'var':
                    roomfeats.append(c.getAttrs()['var'])
                elif name == 'feature':
                    roomfeats.append(c.getAttrs()['var'])
                else:
                    self.printDebug('%s NOT IMPLeMENTED' % name)
            if MUC: # for MODE
                modestr = '+'
                for feat in roomfeats:
                    if feat == 'muc_hidden':
                        modestr += 's'
                    if feat == 'muc_membersonly':
                        modestr += 'p'
                    if feat == 'muc_moderated':
                        modestr += 'm'
                    if feat == 'muc_nonanonymous':
                        modestr += 'A'
                    if feat == 'muc_open':
                        modestr += 'F'
                    if feat == 'muc_passwordprotected':
                        modestr += 'k'
                    if feat == 'muc_persistent':
                        modestr += 'P'
                    if feat == 'muc_public':
                        modestr += 'B'
                    if feat == 'muc_rooms':
                        self.printDebug('muc_rooms not implemented')
                    if feat == 'muc_semianonymous':
                        modestr += 'a'
                    if feat == 'muc_temporary':
                        modestr += 'T'
                    if feat == 'muc_unmoderated':
                        modestr += 'u'
                    if feat == 'muc_unsecured':
                        modestr += 'U'
                self.ircCommandMODEMUC(roomname, modestr)
            else:
                self.printDebug("IQ stuff still missing here")

    def presenceHandler(self, sess, pres):
        """Handle incoming XMPP with type presence

        @type sess: Connection
        @type pres: Presence
        @param sess: XMPP Connection
        @param press: XMPP Presence
        """
        MUC = False
        ptype = pres.getType()
        nick=pres.getFrom()
        tags = pres.getTags('x')
        for tag in tags:
            ns = tag.getNamespace()
            if ns.startswith(NS_MUC):
                MUC = True

        role = pres.getRole()
        affiliation = pres.getAffiliation()
        show = pres.getShow()
        status = pres.getStatus()


        room = unicode(pres.getFrom())
        room = room[:room.find('/')]

        # for affiliation and role changes
        if MUC and \
                self.mucs.has_key(room) and \
                nick in self.mucs[room].keys():
            xrole = self.mucs[room][nick]['role']
            xaffiliation = self.mucs[room][nick]['affiliation']
            if role != xrole: # role has changed
                giver = JID('%s/telepaatti' % room)
                if role.upper() == 'MODERATOR':
                    self.ircCommandMODEMUCUSER(giver, nick, room, '+o')
                    self.ircCommandMODEMUCUSER(giver, nick, room, '-v')
                if role.upper() == 'PARTICIPANT':
                    self.ircCommandMODEMUCUSER(giver, nick, room, '-o')
                    self.ircCommandMODEMUCUSER(giver, nick, room, '+v')
                if role.upper() == 'VISITOR':
                    self.ircCommandMODEMUCUSER(giver, nick, room, '-o')
                    self.ircCommandMODEMUCUSER(giver, nick, room, '-v')
                else:
                    self.printDebug('MODE NONE')
            if xaffiliation != affiliation: # affiliation has changed
                pass

        # for nick changes
        if (pres.getNick() == self.newnick or pres.getNick() == self.nickname)\
                and pres.getStatusCode() == '303':
            self.nickChangeInMucs[room] = {'checked': True,
                                           'changed': True}
            # check if we have checked all MUCs
            for muc in self.nickChangeInMucs.keys():
                if not self.nickChangeInMucs[muc]['checked']:
                    return # no need to go any further
            # check if it changed in all MUCs
            for muc in self.nickChangeInMucs.keys():
                if not self.nickChangeInMucs[muc]['changed']:
                    changedMucs = list()
                    for gei in self.nickChangeInMucs.keys():
                        if self.nickChangeInMucs[gei]['changed']:
                            changedMucs.append(gei)
                    self.nickChangeInMucs = {}
                    for muc2 in changedMucs:
                        self.nickChangeInMucs[muc2] = {'checked': False,
                                                       'changed': False}
                        self.xmppCommandMUCPRESENCE(muc2, self.nickname)
                    self.ircCommandERROR('Nick conflicts in some MUC wont change')
                    return # out
            # remove, all have changed
            self.nickChangeInMucs = {}
            if pres.getNick() == self.nickname:
                self.newnick = ''
                return
            self.sendToIRC(':%s NICK :%s' %
                           (self.nickname,
                            self.newnick))
            for muc in self.getMucs():
                del (self.mucs[muc][JID("%s/%s" % (muc, self.nickname))]) # remove the old
                # add the new
                self.mucs[muc][JID("%s/%s" % (muc, self.newnick))] = { 'role': role,
                                                                       'affiliation': affiliation }
            self.nickname = self.newnick
            self.newnick = ''
            return

        if not MUC and ptype == 'error':
            erc = pres.getErrorCode()
            if erc == '409' and self.mucs.has_key(nick.getStripped()):
                self.nickChangeInMucs[room] = {'checked': True,
                                               'changed': False}
                # all must have come
                self.printDebug(str(self.nickChangeInMucs))
                for muc in self.nickChangeInMucs.keys():
                    if not self.nickChangeInMucs[muc]['checked']:
                        return

                changedMucs = list()
                for gei in self.nickChangeInMucs.keys():
                    if self.nickChangeInMucs[gei]['changed']:
                        changedMucs.append(gei)
                self.nickChangeInMucs = {}
                for muc2 in changedMucs:
                    self.nickChangeInMucs[muc2] = {'checked': False,
                                                   'changed': False}
                    self.xmppCommandMUCPRESENCE(muc2, self.nickname)
                self.ircCommandERROR('Nick conflicts in some MUC wont change')
            else:
                self.ircCommandERROR('Got some error %s' % unicode(pres))
            return

        if not MUC: # normal presence
            if ptype in ['subscribe']:
                self.ircCommandSUBSCRIBE(pres)
            elif ptype in ['unsubscribe']:
                self.ircCommandUNSUBSCRIBE(pres)
            elif ptype in ['subscribed']:
                self.ircCommandSUBSCRIBED(pres)
            elif ptype in ['unsubscribed']:
                self.ircCommandUNSUBSCRIBED(pres)
            else:
                self.ircCommandROSTERMSG(pres)

        elif MUC:
            if ptype == 'error':
                er = pres.getError()
                erc = pres.getErrorCode()
                if erc == '401':
                    self.ircCommandERRORMUC(475, 'Password requeired to join', room)
                elif erc == '403':
                    self.ircCommandERRORMUC(474, 'Cannot join MUC, you are banned', room)
                elif erc == '404':
                    self.ircCommandERRORMUC(404, 'No such MUC', room)
                elif erc == '405':
                    self.ircCommandERRORMUC(478, 'Can\'t create MUC', room)
                elif erc == '406':
                    self.ircCommandERRORMUC(437, 'You must use reserverd nick to enter', room)
                elif erc == '407':
                    self.ircCommandERRORMUC(473, 'Must be a member to enter', room)
                elif erc == '409':
                    self.ircCommandERRORMUC(437, 'You must change nickname to enter', room)
                elif erc == '503':
                    self.ircCommandERRORMUC(471, 'MUC is full', room)
                else:
                    self.ircCommandERROR('MUC error not yet implemented')
            else:
                joining = self.joinQueue.has_key(room)
                inroom = self.mucs.has_key(room)
                if ptype == 'unavailable':
                    self.printDebug('unavailable')
                    if nick.getResource() == self.nickname:
                        self.printDebug('our self')
                        if joining:
                            del (self.joinQueue[room])
                        elif self.nickChangeInMucs.has_key(room):
                            # between nick change
                            self.printDebug('we are between nick change')
                            return
                        elif inroom:
                            self.ircCommandPART(nick, room, ' left')
                            del (self.mucs[room])
                        else:
                            line = "%s is doing something" % nick
                            self.printDebug(line.encode('utf-8'))
                    else: # someonerin else
                        if joining:
                            self.printDebug("%s left while we are joining room %s" % (
                                nick, room))
                        elif inroom:
                            self.ircCommandPART(nick, room, 'left')
                            del (self.mucs[room][nick])
                        else:
                            line = "%s is doing something" % nick
                            self.printDebug(line.encode('utf-8'))
                else: # not unavailable type
                    self.printDebug('not unavailable')
                    if nick.getResource() == self.nickname:
                        if joining:
                            # fix this also later
                            self.mucs[room] = self.joinQueue[room]['users']
                            self.mucs[room][JID("%s/%s" % (room, self.nickname))] = { 'role': role,
                                                                                      'affiliation': affiliation,
                                                                                      'show' : show,
                                                                                      'status': status}
                            del(self.joinQueue[room])
                            self.ircCommandSELFJOIN(self.nickname, room)
                        elif inroom:
                            self.mucs[room][JID("%s/%s" % (room, self.nickname))] = { 'role': role,
                                                                                      'affiliation': affiliation,
                                                                                      'show' : show,
                                                                                      'status': status}
                        else:
                            line = "%s is doing something" % nick
                            self.printDebug(line.encode('utf-8'))
                    elif nick.getResource() == self.newnick:
                        pass
                    else: # someone else
                        if joining:
                            if nick not in self.joinQueue[room]['users'].keys():
                                self.joinQueue[room]['users'][nick] = { 'role': role,
                                                                        'affiliation': affiliation,
                                                                        'show' : show,
                                                                        'status': status}
                        elif inroom:
                            if nick not in self.mucs[room].keys():
                                self.mucs[room][nick] = { 'role': role,
                                                          'affiliation': affiliation,
                                                          'show' : show,
                                                          'status': status }
                                self.ircCommandJOIN(nick, room)
                            else:
                                self.mucs[room][nick] = { 'role': role,
                                                          'affiliation': affiliation,
                                                          'show' : show,
                                                          'status': status }
                        else:
                            self.printDebug('TROUBLE LINE')

    def commandHandler(self, data):
        """Command handler for commands and text coming in from IRC-client

        @type data: string
        @param data: IRC data coming from IRC-client
        """
        self.printDebug('got ircline: %s' % data)
        # utf-8 test
        try:
            unicode(data, 'utf-8')
        except exceptions.UnicodeDecodeError:
            self.printError('GOT ERROR')
            self.ircCommandERROR('Input form IRC client was not in utf-8. Turn utf-8 support on from your IRC client or input only pure ascii',-1)
            return

        args = data.split(' ', 1)
        arguments = u''
        command = args[0]
        if len(args) == 2:
            arguments = args[1]
        command = command.upper()
        arguments = arguments.strip()
        MUC = False
        if arguments.startswith('#'):
            arguments = arguments[1:]
            MUC = True

        if command == 'JOIN':
            room = arguments
            if room == 'roster':
                if self.joinedRoster: # already in #roster
                    return
                self.ircCommandROSTERSELFJOIN()
            else:
                if room.find('@') < 1:
                    self.ircCommandERRORMUC(404, 'No such MUC', room)
                    return
                if room in self.mucs.keys(): # already in MUC
                    return
                self.joinQueue[arguments] = {'messages': list(),
                                             'users': {}}
                p=Presence(to='%s/%s' % (
                        room,
                        self.nickname))
                p.setTag('x',namespace=NS_MUC).setTagData('password','')
                p.getTag('x').addChild('history',{'maxchars':'10000','maxstanzas':'100'})
                self.sendToXMPP(p)


        elif command == 'PART':
            x = arguments.find(' :')
            room = arguments.strip()
            text = ''
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                room = arguments[:x]
                room = room.strip()
            if room == 'roster':
                if not self.joinedRoster: # not in roster
                    return
                self.ircCommandROSTERPART()
            else:
                if room not in self.mucs.keys(): # not in room
                    return
                self.sendToXMPP(Presence(to='%s/%s' % (room, self.newnick),
                                         typ='unavailable',
                                         status=text))

        elif command == 'PRIVMSG':
            x = arguments.find(' :')
            jid = arguments.strip()
            text = ''
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                sact = text.find('\001ACTION ')
                eact = text.rfind('\001')
                if sact > -1 and eact > -1:
                    text = '/me %s' % text[sact+8:eact]
                jid = arguments[:x]
                jid = jid.strip()
            type = 'chat'
            if MUC:
                type = 'groupchat'
            at = jid.find('@')
            slash = jid.find('/')

            if jid == 'roster':
                self.ircCommandROSTERPRIVMSGMUC(text)
                return
            elif (at < 0) and not MUC: # private msg from muc
                self.ircCommandERROR('You are trying to send private message someone in MUC room. Jabber can\'t send messages with nick only. Try to sen message to whole MUC jid, for example if you are in room jabber@conference.jabber.org and are trying to send message to nick petteri use /msg jabber@conference.jabber.org/petteri message!')
                targetnicks = list()
                for muc in self.mucs.iterkeys():
                    for mn in self.mucs[muc].keys():
                        mn = unicode(mn)
                        mn = mn.encode('utf-8')
                        if mn[mn.find('/')+1:] == jid:
                            targetnicks.append(mn)
                if len(targetnicks) != 1:
                    self.printError('Problems')
                else:
                    jid = targetnicks[0]
                    self.ircCommandERROR('Telepaatti forwarded your message to JID: %s' % jid)
            self.sendToXMPP(protocol.Message(jid,
                                             text,
                                             typ = type))

        elif command == 'NICK':
            if self.notFirstNick:
                self.newnick = arguments
                for muc in self.getMucs():
                    self.nickChangeInMucs[muc] = {'checked': False,
                                                  'changed': False}
                for muc in self.nickChangeInMucs.keys():
                    self.xmppCommandMUCPRESENCE(muc, self.newnick)
            else:
                self.notFirstNick = True

        elif command == 'TOPIC':
            x = arguments.find(' :')
            jid = arguments.strip()
            text = ''
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                jid = arguments[:x]
                jid = jid.strip()
            if jid not in self.mucs.keys():
                self.ircCommandERROR('', 403)
                return
            if jid == 'roster':
                self.ircCommandERRORMUC(482, 'TOPIC ON ROSTER CANNOT BE CHANGED', jid)
                return

            self.sendToXMPP(protocol.Message(jid,
                                             typ = 'groupchat',
                                             subject = text))

        elif command == 'MODE':
            if not arguments:
                return
            x = arguments.find(' ')
            params = ''
            jid = arguments
            if x > -1:
                jid = arguments[:x]
                params = arguments[x+1:]
            x = params.find(' ')
            tonick = ''
            if x > -1:
                tonick = params[x+1:]
                params = params[x:]
                params.strip()


            if jid == 'roster':
                return
            elif jid == self.nickname:
                self.ircCommandMODE(params)
            else:
                if params.find('b') > -1: # get bandlist
                    self.ircCommandMODEMUCBANLIST(jid)
                    return
                elif params.find('+o') > -1: # trying to op someone
                    self.xmppCommandMUCROLE(jid, tonick, 'moderator')
                elif params.find('-o') > -1: # trying to deop someone
                    self.xmppCommandMUCROLE(jid, tonick, 'participant')
                elif params.find('+v') > -1: # trying to voice someone
                    self.xmppCommandMUCROLE(jid, tonick, 'participant')
                elif params.find('-v') > -1: # trying to voice someone
                    self.xmppCommandMUCROLE(jid, tonick, 'visitor')
                else:
                    self.xmppCommandMUCMODE(jid)

        elif command == 'WHO':
            if not arguments:
                return
            jid = arguments
            self.xmppCommandMUCUSERS(jid)

        elif command == 'WHOIS':
            self.ircCommandWHOIS(JID(arguments))
            self.xmppCommandGETWHOIS(JID(arguments))

        elif command == 'AWAY':
            arguments = arguments[1:] # remove the :
            show = ''
            if arguments != '':
                show = 'away'
            args = arguments.split(' ',1)
            status = arguments
            if args[0].upper() in STATUSSTATES:
                show = args[0]
                if len(args) == 2:
                    status = args[1]
            self.xmppCommandSTATUS(show, status)

def usage():
    """Usage function for showing commandline options """
    print "Usage: telepaatti [OPTION]..."
    print "OPTIONS"
    print "-h, --help\t telepaatti help"
    print "-p, --port\t port which telepaatti listens"
    print "-u, --user\t Jabber/XMPP username in the format name@xmppserver.tld"
    print "-w, --password\t Password for Jabber/XMPP account"
    print "-d, --debug\t turn debug messages on"

def main():
    """Main function where the control flow stars """
    port = 6667
    user = ''
    password = ''
    debug = False
    try:
        opts, args = getopt.getopt(sys.argv[1:],
                                   "u:p:w:h:d",
                                   ["user=",
                                    "port=",
                                    "password=",
                                    "help",
                                    "debug"])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    if len(opts) == 0:
        usage()
        sys.exit(2)
    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit()
        if o in ("-p", "--port"):
            try:
                port = int(a)
            except:
                print "port should be an integer"
                sys.exit()
        if o in ("-u", "--user"):
            if a.find('@') < 1:
                print "user name should be in form user@xmppserver.tld"
                sys.exit()
            else:
                user = a
        if o in ("-w", "--password"):
            password = a
        if o in ("-d", "--debug"):
            print "debug messages on"
            debug = True

    service = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    service.bind(("", port))
    service.listen(1)

    print "listening on port", port

    (clientsocket, address ) = service.accept()
    ct = ClientThread(clientsocket, port, user, password, debug)
    ct.start()
    service.shutdown(socket.SHUT_RDWR)

if __name__ == "__main__":
    main()
