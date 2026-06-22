import {keyOfElement} from './buttons';
import {state} from './update';
import {warningToastWithBar, errorToast} from '../base';
import {setStoredVote} from './vote-state';

/** Adds handlers to buttons that are visible when voting is enabled. */
export function onReady() {
  // Use a token bucket implementation to allow 10 Votes per minute.
  const maxTokens = 10;
  let currentTokens = maxTokens;
  const bucketLifetime = 30000; // half a minute
  let currentBucket = $.now();

  // Search/request already works on mobile because it uses click/tap.
  // Votes need the same mobile-safe path, with touch/pointer dedupe.
  const activationEvents = 'click tap touchend pointerup';
  let lastActivationAt = 0;
  let lastActivationSignature = '';

  function canVote() {
    const now = $.now();
    const timePassed = now - currentBucket;

    if (timePassed > bucketLifetime) {
      currentBucket = now;
      currentTokens = maxTokens - 1;
      return true;
    }

    if (currentTokens > 0) {
      currentTokens--;
      return true;
    }

    const ratio = (bucketLifetime - timePassed) / bucketLifetime;
    warningToastWithBar('You\'re doing that too often');
    $('#vote-timeout-bar').css('transition', 'none');
    $('#vote-timeout-bar').css('width', ratio * 100 + '%');
    $('#vote-timeout-bar')[0].offsetHeight;
    $('#vote-timeout-bar').css({
      'transition': 'width ' + ratio * bucketLifetime / 1000 + 's linear',
      'width': '0%',
    });
    return false;
  }

  function vote(button, key, amount, onFail = null) {
    let votes = button.closest('.queue-entry').find('.queue-vote-count');
    if (votes.length == 0) {
      votes = button.siblings('#current-song-votes');
    }

    const currentVotes = Number(votes.text()) || 0;
    votes.text(String(currentVotes + amount));

    $.post(urls['musiq']['vote'], {
      key: key,
      amount: amount,
    }).fail(function(response) {
      errorToast(response.responseText || 'Could not register vote');

      const failedVotes = Number(votes.text()) || 0;
      votes.text(String(failedVotes - amount));

      if (onFail) {
        onFail();
      }
    });
  }

  function triggerVoteAnimation(buttonElement) {
    if (!buttonElement || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }

    buttonElement.classList.remove('furatic-vote-bump');
    void buttonElement.offsetWidth;
    buttonElement.classList.add('furatic-vote-bump');

    window.setTimeout(function() {
      buttonElement.classList.remove('furatic-vote-bump');
    }, 560);
  }

  function activationSignature(buttonElement) {
    const button = $(buttonElement).closest('.vote-up, .vote-down');
    const keyedElement = button.closest('[data-queue-key]');
    const key = keyedElement.attr('data-queue-key') || '';
    const id = button.attr('id') || '';
    const direction = button.hasClass('vote-up') ? 'up' : 'down';

    return direction + ':' + key + ':' + id;
  }

  function shouldIgnoreActivation(event, buttonElement) {
    const originalEvent = event.originalEvent || event;

    if (
      event.type === 'pointerup' &&
      originalEvent &&
      originalEvent.pointerType === 'mouse' &&
      originalEvent.button !== 0
    ) {
      return true;
    }

    const now = Date.now();
    const signature = activationSignature(buttonElement);

    if (signature === lastActivationSignature && now - lastActivationAt < 520) {
      return true;
    }

    lastActivationSignature = signature;
    lastActivationAt = now;

    if (!originalEvent || originalEvent.cancelable !== false) {
      event.preventDefault();
    }
    event.stopPropagation();

    return false;
  }

  function resolveVoteKey(button) {
    if (button.closest('#current-song-card').length > 0) {
      if (state == null || state.currentSong == null) {
        return -1;
      }
      return state.currentSong.queueKey;
    }

    return keyOfElement(button);
  }

  function handleVotePress(buttonElement, direction) {
    const button = $(buttonElement);

    if (button.attr('data-furatic-own-vote-blocked') === 'true') {
      return;
    }

    const key = resolveVoteKey(button);
    if (key == -1) {
      return;
    }

    if (!canVote()) {
      return;
    }

    const up = direction === 'up' ? button : button.siblings('.vote-up');
    const down = direction === 'down' ? button : button.siblings('.vote-down');
    const previousState = up.hasClass('pressed') ? '+' : down.hasClass('pressed') ? '-' : '0';

    function applyVisualVoteState(value) {
      up.removeClass('pressed');
      down.removeClass('pressed');

      if (value === '+') {
        up.addClass('pressed');
      } else if (value === '-') {
        down.addClass('pressed');
      }

      setStoredVote(key, value);
    }

    function restorePreviousState() {
      applyVisualVoteState(previousState);
    }

    triggerVoteAnimation(buttonElement);

    if (direction === 'up') {
      if (up.hasClass('pressed')) {
        applyVisualVoteState('0');
        vote(button, key, -1, restorePreviousState);
      } else {
        applyVisualVoteState('+');
        vote(button, key, down.hasClass('pressed') ? 2 : 1, restorePreviousState);
      }
      return;
    }

    if (down.hasClass('pressed')) {
      applyVisualVoteState('0');
      vote(button, key, 1, restorePreviousState);
    } else {
      applyVisualVoteState('-');
      vote(button, key, up.hasClass('pressed') ? -2 : -1, restorePreviousState);
    }
  }

  $('#content').on(activationEvents, '.vote-up, .vote-down', function(event) {
    if ($(this).attr('data-furatic-own-vote-blocked') === 'true') {
      return;
    }

    if (shouldIgnoreActivation(event, this)) {
      return;
    }

    if ($(this).hasClass('vote-up')) {
      handleVotePress(this, 'up');
    } else {
      handleVotePress(this, 'down');
    }
  });
}

$(document).ready(() => {
  if (!["/musiq/", "/p/"].includes(window.location.pathname)) {
    return;
  }

  onReady();
});
